/**
 * Albacore — single Teensy 4.1 control + sensing firmware.
 * Protocol matches jetson/comms/protocol.py (ASCII lines, newline-terminated).
 *
 * Build: Teensy 4.1, Arduino framework, hardware UART to Jetson.
 * Libraries: Servo, Wire, SparkFun_LSM6DSV16X, MS5837 (BlueRobotics).
 */

#include <Arduino.h>
#include <MS5837.h>
#include <Servo.h>
#include <SparkFun_LSM6DSV16X.h>
#include <Wire.h>
#include <cstring>
#include <cstdio>

// Set via PlatformIO: `-DSERIAL_DEBUG=1` or env `teensy41_debug`. When enabled, emits rare `DBG,...`
// lines for bring-up (Jetson parser ignores unknown types until extended).
#ifndef SERIAL_DEBUG
#define SERIAL_DEBUG 0
#endif

// Jetson command/telemetry transport UART (set once here if EE remaps ports).
#define JETSON_SERIAL Serial1

// ── PIN ASSIGNMENTS (confirm with EE) ─────────────────────────────────────
constexpr uint8_t MAIN_THRUSTER_PIN = 2;
constexpr uint8_t BOW_THRUSTER_PIN = 3;
constexpr uint8_t RUDDER_SERVO_PIN = 4;
constexpr uint8_t RUDDER_SERVO_2_PIN = 5;
constexpr uint8_t ELEVATOR_SERVO_PIN = 6;
constexpr uint8_t ELEVATOR_SERVO_2_PIN = 7;
constexpr uint8_t ULTRASONIC_TRIG_PIN = 8;
constexpr uint8_t ULTRASONIC_ECHO_PIN = 9;
constexpr uint8_t BALLAST_IN1 = 10;
constexpr uint8_t BALLAST_IN2 = 11;

constexpr uint8_t BATTERY_PIN = A8;

// Paired fins: set true to drive RUDDER_SERVO_2 / ELEVATOR_SERVO_2 with same µs
constexpr bool USE_PAIRED_RUDDER = false;
constexpr bool USE_PAIRED_ELEVATOR = false;

// Timing
// Must match jetson/config.py CONTROL_BAUD exactly; this is real UART timing now.
constexpr unsigned long SERIAL_BAUD = 1000000;
constexpr unsigned long TELEMETRY_PERIOD_MS = 50;
constexpr unsigned long WATCHDOG_TIMEOUT_MS = 500;
constexpr unsigned long ESC_ARM_HOLD_MS = 2000;
constexpr unsigned long ULTRASONIC_ECHO_TIMEOUT_US = 25000;

constexpr int ESC_NEUTRAL_US = 1500;
constexpr int ESC_MIN_US = 1000;
constexpr int ESC_MAX_US = 2000;

// Temporary bench test: gently drive main thruster (pin 2) at boot to verify ESC/motor path.
constexpr bool TEST_MAIN_THRUSTER_ON_BOOT = true;
constexpr int TEST_MAIN_THRUSTER_PCT = 10;                  // 10% -> 1550us
constexpr unsigned long TEST_MAIN_THRUSTER_MS = 1200;       // short pulse for safe bench checks

// Battery: 10-sample rolling average; divider ratio (tune for harness)
constexpr uint8_t BAT_AVG_N = 10;
constexpr float BATTERY_DIVIDER_RATIO = 4.0f;

// IMU: SparkFun driver returns accel in mg, gyro in mdps
constexpr float MG_TO_MPS2 = 9.80665f / 1000.0f;
constexpr float MDPS_TO_DPS = 1.0f / 1000.0f;

// -----------------------------------------------------------------------------
Servo mainThruster;
Servo bowThruster;
Servo rudderServo;
Servo rudderServo2;
Servo elevatorServo;
Servo elevatorServo2;

SparkFun_LSM6DSV16X imu;
MS5837 depthSensor;

bool imuReady = false;
bool hasDepthSensor = false;

sfe_lsm_data_t imuAccel{};
sfe_lsm_data_t imuGyro{};

int ussTopCm = -1;
int ussLeftCm = -1;
int ussRightCm = -1;
int ussFrontCm = -1;

float batReadings[BAT_AVG_N];
uint8_t batIdx = 0;
bool batBufFilled = false;

unsigned long lastCmdMillis = 0;
unsigned long lastTelemetryMillis = 0;
bool firstCmdReceived = false;
bool watchdogActive = false;

int cmdThrusterPct = 0;
int cmdBowPct = 0;
int cmdRudderDeg = 0;
int cmdElevatorDeg = 0;
int cmdBallastDir = 0;

char rxLineBuf[128];
size_t rxLineLen = 0;

void applyActuatorsFromCommands();
void setBallastFromDir(int dir);

static int clampInt(int v, int lo, int hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

static int mapPctToUs(int pct) {
  pct = clampInt(pct, -100, 100);
  return ESC_NEUTRAL_US + (pct * 5);
}

static int mapDegToUs(int deg) {
  deg = clampInt(deg, -45, 45);
  return map(deg, -45, 45, ESC_MIN_US, ESC_MAX_US);
}

void setBallastFromDir(int dir) {
  dir = clampInt(dir, -1, 1);
  // 1 = fill/descend: IN1 HIGH, IN2 LOW
  // -1 = empty/ascend: IN1 LOW, IN2 HIGH
  // 0 = stop
  if (dir > 0) {
    digitalWrite(BALLAST_IN1, HIGH);
    digitalWrite(BALLAST_IN2, LOW);
  } else if (dir < 0) {
    digitalWrite(BALLAST_IN1, LOW);
    digitalWrite(BALLAST_IN2, HIGH);
  } else {
    digitalWrite(BALLAST_IN1, LOW);
    digitalWrite(BALLAST_IN2, LOW);
  }
}

void writeFinServosMicroseconds(int rudderUs, int elevatorUs) {
  rudderUs = clampInt(rudderUs, ESC_MIN_US, ESC_MAX_US);
  elevatorUs = clampInt(elevatorUs, ESC_MIN_US, ESC_MAX_US);
  rudderServo.writeMicroseconds(rudderUs);
  elevatorServo.writeMicroseconds(elevatorUs);
  if (USE_PAIRED_RUDDER) rudderServo2.writeMicroseconds(rudderUs);
  if (USE_PAIRED_ELEVATOR) elevatorServo2.writeMicroseconds(elevatorUs);
}

// Apply the commands to the actuators
void applyActuatorsFromCommands() {
  mainThruster.writeMicroseconds(clampInt(mapPctToUs(cmdThrusterPct), ESC_MIN_US, ESC_MAX_US));
  bowThruster.writeMicroseconds(clampInt(mapPctToUs(cmdBowPct), ESC_MIN_US, ESC_MAX_US));
  int rudUs = mapDegToUs(cmdRudderDeg);
  int elevUs = mapDegToUs(cmdElevatorDeg);
  writeFinServosMicroseconds(rudUs, elevUs);
  setBallastFromDir(cmdBallastDir);
}

void applyWatchdogFailsafe() {
  cmdThrusterPct = 0;
  cmdBowPct = 0;
  cmdRudderDeg = 0;
  cmdElevatorDeg = 0;
  cmdBallastDir = -1;
  mainThruster.writeMicroseconds(ESC_NEUTRAL_US);
  bowThruster.writeMicroseconds(ESC_NEUTRAL_US);
  writeFinServosMicroseconds(ESC_NEUTRAL_US, ESC_NEUTRAL_US);
  setBallastFromDir(-1);
}

int readUltrasonicCm(uint8_t trigPin, uint8_t echoPin) {
  // HC-SR04 style timing: round trip µs / 58 ~= distance in cm.
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  const unsigned long pulse = pulseIn(echoPin, HIGH, ULTRASONIC_ECHO_TIMEOUT_US);
  if (pulse == 0) return -1;

  const int cm = static_cast<int>(pulse / 58UL);
  return (cm > 0) ? cm : -1;
}

bool tryInitImu() {
  if (!imu.begin(Wire)) {
    return false;
  }
  imu.enableBlockDataUpdate();
  imu.setAccelDataRate(LSM6DSV16X_ODR_AT_120Hz);
  imu.setGyroDataRate(LSM6DSV16X_ODR_AT_120Hz);
  imu.setAccelFullScale(LSM6DSV16X_16g);
  imu.setGyroFullScale(LSM6DSV16X_2000dps);
  return true;
}

void readImuTelemetry(float *ax, float *ay, float *az, float *gx, float *gy, float *gz) {
  *ax = *ay = *az = *gx = *gy = *gz = 0.0f;
  if (!imuReady) return;
  if (!imu.getAccel(&imuAccel) || !imu.getGyro(&imuGyro)) {
    return;
  }
  *ax = imuAccel.xData * MG_TO_MPS2;
  *ay = imuAccel.yData * MG_TO_MPS2;
  *az = imuAccel.zData * MG_TO_MPS2;
  *gx = imuGyro.xData * MDPS_TO_DPS;
  *gy = imuGyro.yData * MDPS_TO_DPS;
  *gz = imuGyro.zData * MDPS_TO_DPS;
}

float readBatteryVoltageAvg() {
  noInterrupts();
  const int raw = analogRead(BATTERY_PIN);
  interrupts();
  float vAdc = (raw / 4095.0f) * 3.3f;
  float vBatt = vAdc * BATTERY_DIVIDER_RATIO;
  batReadings[batIdx] = vBatt;
  batIdx = static_cast<uint8_t>((batIdx + 1) % BAT_AVG_N);
  if (batIdx == 0) {
    batBufFilled = true;
  }
  const uint8_t n = batBufFilled ? BAT_AVG_N : batIdx;
  if (n == 0) {
    return vBatt;
  }
  float sum = 0.0f;
  for (uint8_t i = 0; i < n; ++i) {
    sum += batReadings[i];
  }
  return sum / static_cast<float>(n);
}

void parseCmdLine(const char *line) {
  if (strncmp(line, "CMD,", 4) != 0) return;

  int t = 0, b = 0, r = 0, e = 0, bal = 0;
  const int n = sscanf(line, "CMD,%d,%d,%d,%d,%d", &t, &b, &r, &e, &bal);
  if (n != 5) return;

  cmdThrusterPct = clampInt(t, -100, 100);
  cmdBowPct = clampInt(b, -100, 100);
  cmdRudderDeg = clampInt(r, -45, 45);
  cmdElevatorDeg = clampInt(e, -45, 45);
  cmdBallastDir = clampInt(bal, -1, 1);

  firstCmdReceived = true;
  watchdogActive = false;
  lastCmdMillis = millis();
  applyActuatorsFromCommands();
#if SERIAL_DEBUG
  Serial.print(F("DBG,CMD_ACK,"));
  Serial.print(cmdThrusterPct);
  Serial.print(',');
  Serial.print(cmdBowPct);
  Serial.print(',');
  Serial.print(cmdRudderDeg);
  Serial.print(',');
  Serial.print(cmdElevatorDeg);
  Serial.print(',');
  Serial.print(cmdBallastDir);
  Serial.print('\n');
#endif
}

void pumpSerialRx() {
  while (JETSON_SERIAL.available() > 0) {
    const char c = static_cast<char>(JETSON_SERIAL.read());
    if (c == '\n') {
      if (rxLineLen < sizeof(rxLineBuf) - 1) {
        rxLineBuf[rxLineLen] = '\0';
        parseCmdLine(rxLineBuf);
      }
      rxLineLen = 0;
    } else if (c != '\r') {
      if (rxLineLen < sizeof(rxLineBuf) - 1) {
        rxLineBuf[rxLineLen++] = c;
      } else {
        rxLineLen = 0;
      }
    }
  }
}

void sendTelemetryBlock() {
  float ax = 0.0f, ay = 0.0f, az = 0.0f, gx = 0.0f, gy = 0.0f, gz = 0.0f;
  readImuTelemetry(&ax, &ay, &az, &gx, &gy, &gz);

  JETSON_SERIAL.print(F("IMU,"));
  JETSON_SERIAL.print(ax, 4);
  JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(ay, 4);
  JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(az, 4);
  JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(gx, 4);
  JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(gy, 4);
  JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(gz, 4);
  JETSON_SERIAL.print('\n');

  JETSON_SERIAL.print(F("USS,"));
  JETSON_SERIAL.print(ussTopCm);
  JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(ussLeftCm);
  JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(ussRightCm);
  JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(ussFrontCm);
  JETSON_SERIAL.print('\n');

  const float batV = readBatteryVoltageAvg();
  JETSON_SERIAL.print(F("BAT,"));
  JETSON_SERIAL.print(batV, 3);
  JETSON_SERIAL.print('\n');

  float depthM = 0.0f;
  if (hasDepthSensor) {
    depthSensor.read();
    depthM = depthSensor.depth();
  }
  JETSON_SERIAL.print(F("DEP,"));
  JETSON_SERIAL.print(depthM, 3);
  JETSON_SERIAL.print('\n');
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  JETSON_SERIAL.begin(SERIAL_BAUD);
  analogReadResolution(12);
  pinMode(BATTERY_PIN, INPUT);
  pinMode(ULTRASONIC_TRIG_PIN, OUTPUT);
  pinMode(ULTRASONIC_ECHO_PIN, INPUT);
  digitalWrite(ULTRASONIC_TRIG_PIN, LOW);

  mainThruster.attach(MAIN_THRUSTER_PIN);
  bowThruster.attach(BOW_THRUSTER_PIN);
  rudderServo.attach(RUDDER_SERVO_PIN);
  elevatorServo.attach(ELEVATOR_SERVO_PIN);
  if (USE_PAIRED_RUDDER) {
    rudderServo2.attach(RUDDER_SERVO_2_PIN);
  }
  if (USE_PAIRED_ELEVATOR) {
    elevatorServo2.attach(ELEVATOR_SERVO_2_PIN);
  }

  pinMode(BALLAST_IN1, OUTPUT);
  pinMode(BALLAST_IN2, OUTPUT);
  digitalWrite(BALLAST_IN1, LOW);
  digitalWrite(BALLAST_IN2, LOW);

  mainThruster.writeMicroseconds(ESC_NEUTRAL_US);
  bowThruster.writeMicroseconds(ESC_NEUTRAL_US);
  writeFinServosMicroseconds(ESC_NEUTRAL_US, ESC_NEUTRAL_US);

  delay(ESC_ARM_HOLD_MS);

  if (TEST_MAIN_THRUSTER_ON_BOOT) {
    mainThruster.writeMicroseconds(clampInt(mapPctToUs(TEST_MAIN_THRUSTER_PCT), ESC_MIN_US, ESC_MAX_US));
    delay(TEST_MAIN_THRUSTER_MS);
    mainThruster.writeMicroseconds(ESC_NEUTRAL_US);
#if SERIAL_DEBUG
    Serial.print(F("DBG,BOOT_MAIN_THR_TEST,"));
    Serial.print(TEST_MAIN_THRUSTER_PCT);
    Serial.print('\n');
#endif
  }

  Wire.begin();
  Wire.setClock(400000);

  imuReady = tryInitImu();

  if (depthSensor.init()) {
    hasDepthSensor = true;
    depthSensor.setFluidDensity(997);
  } else {
    hasDepthSensor = false;
  }

  for (uint8_t i = 0; i < BAT_AVG_N; ++i) {
    batReadings[i] = 0.0f;
  }

  lastCmdMillis = millis();
  lastTelemetryMillis = millis();
}

void loop() {
  const unsigned long now = millis();

  // Parse commands from the serial port
  pumpSerialRx();

  if (firstCmdReceived && !watchdogActive && (now - lastCmdMillis) > WATCHDOG_TIMEOUT_MS) {
    applyWatchdogFailsafe();
    watchdogActive = true;
#if SERIAL_DEBUG
    Serial.println(F("DBG,WD"));
#endif
  }

  if (now - lastTelemetryMillis >= TELEMETRY_PERIOD_MS) {
    lastTelemetryMillis = now;
    ussTopCm = readUltrasonicCm(ULTRASONIC_TRIG_PIN, ULTRASONIC_ECHO_PIN);
    ussLeftCm = -1;
    ussRightCm = -1;
    ussFrontCm = -1;
    sendTelemetryBlock();
  }

}
