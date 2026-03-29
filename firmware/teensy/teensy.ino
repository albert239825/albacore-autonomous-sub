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
#include <cstdio>
#include <cstring>

#ifndef SERIAL_DEBUG
#define SERIAL_DEBUG 0
#endif

#define JETSON_SERIAL Serial6

// ── PIN ASSIGNMENTS (confirm with EE) ─────────────────────────────────────
constexpr uint8_t MAIN_THRUSTER_PIN    = 2;
constexpr uint8_t BOW_THRUSTER_PIN     = 3;
constexpr uint8_t RUDDER_SERVO_PIN     = 4;
constexpr uint8_t RUDDER_SERVO_2_PIN   = 5;
constexpr uint8_t ELEVATOR_SERVO_PIN   = 6;
constexpr uint8_t ELEVATOR_SERVO_2_PIN = 7;
constexpr uint8_t ULTRASONIC_TRIG_PIN  = 8;
constexpr uint8_t ULTRASONIC_ECHO_PIN  = 9;

// Ballast: 2 ballasts × 2 motors × 2 H-bridge pins = 8 pins
constexpr uint8_t BALLAST1_MOTOR1_PIN1 = 10;
constexpr uint8_t BALLAST1_MOTOR1_PIN2 = 11;
constexpr uint8_t BALLAST1_MOTOR2_PIN1 = 12;
constexpr uint8_t BALLAST1_MOTOR2_PIN2 = 13;
constexpr uint8_t BALLAST2_MOTOR1_PIN1 = 14;
constexpr uint8_t BALLAST2_MOTOR1_PIN2 = 15;
constexpr uint8_t BALLAST2_MOTOR2_PIN1 = 16;
constexpr uint8_t BALLAST2_MOTOR2_PIN2 = 17;

constexpr uint8_t BATTERY_PIN = A8;

constexpr bool USE_PAIRED_RUDDER   = true;
constexpr bool USE_PAIRED_ELEVATOR = true;

// Timing — SERIAL_BAUD must match jetson/config.py CONTROL_BAUD
constexpr unsigned long SERIAL_BAUD              = 1000000;
constexpr unsigned long TELEMETRY_PERIOD_MS      = 50;
constexpr unsigned long WATCHDOG_TIMEOUT_MS      = 500;
constexpr unsigned long ESC_ARM_HOLD_MS          = 5000;
constexpr unsigned long ULTRASONIC_ECHO_TIMEOUT_US = 25000;

constexpr int ESC_NEUTRAL_US = 1500;
constexpr int ESC_MIN_US     = 1000;
constexpr int ESC_MAX_US     = 2000;
constexpr int ESC_CENTER_US  = (ESC_MIN_US + ESC_MAX_US) / 2;

// Optional per-servo trims (microseconds). Keep at 0 unless mechanical centering needs offset.
constexpr int RUDDER_TRIM_US_1   = -80;
constexpr int RUDDER_TRIM_US_2   = -80;
constexpr int ELEVATOR_TRIM_US_1 = 0;
constexpr int ELEVATOR_TRIM_US_2 = 0;

// Opposite-side control surfaces should generally be mirrored.
constexpr bool MIRROR_RUDDER_2   = true;
constexpr bool MIRROR_ELEVATOR_2 = true;

constexpr bool          TEST_MAIN_THRUSTER_ON_BOOT = true;
constexpr int           TEST_MAIN_THRUSTER_PCT     = 10;
constexpr unsigned long TEST_MAIN_THRUSTER_MS      = 1200;

constexpr uint8_t BAT_AVG_N             = 10;
constexpr float    BATTERY_DIVIDER_RATIO = 4.0f;

constexpr float MG_TO_MPS2  = 9.80665f / 1000.0f;
constexpr float MDPS_TO_DPS = 1.0f / 1000.0f;

// ── GLOBALS ───────────────────────────────────────────────────────────────
Servo mainThruster;
Servo bowThruster;
Servo rudderServo;
Servo rudderServo2;
Servo elevatorServo;
Servo elevatorServo2;

SparkFun_LSM6DSV16X imu;
MS5837 depthSensor;

bool imuReady      = false;
bool hasDepthSensor = false;

sfe_lsm_data_t imuAccel{};
sfe_lsm_data_t imuGyro{};

int ussTopCm = -1;

float   batReadings[BAT_AVG_N]{};
uint8_t batIdx       = 0;
bool    batBufFilled = false;

unsigned long lastCmdMillis       = 0;
unsigned long lastTelemetryMillis = 0;
bool firstCmdReceived = false;
bool watchdogActive   = false;

int cmdThrusterPct = 0;
int cmdBowPct      = 0;
int cmdRudderDeg   = 0;
int cmdElevatorDeg = 0;
int cmdBallastDir  = 0;

char   rxLineBuf[128];
size_t rxLineLen = 0;

// ── HELPERS ───────────────────────────────────────────────────────────────

static int clampInt(int v, int lo, int hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

static int mapPctToUs(int pct) {
  return ESC_NEUTRAL_US + (clampInt(pct, -100, 100) * 5);
}

static int mapDegToUs(int deg) {
  return map(clampInt(deg, -45, 45), -45, 45, ESC_MIN_US, ESC_MAX_US);
}

static int clampPulseUs(int us) {
  return clampInt(us, ESC_MIN_US, ESC_MAX_US);
}

static int mirrorPulseUs(int us) {
  return (2 * ESC_CENTER_US) - us;
}

static void setMotorDir(uint8_t pin1, uint8_t pin2, int dir) {
  // +1 forward, -1 reverse, 0 brake-low
  digitalWrite(pin1, (dir > 0) ? HIGH : LOW);
  digitalWrite(pin2, (dir < 0) ? HIGH : LOW);
}

void setBallastFromDir(int dir) {
  dir = clampInt(dir, -1, 1);
  setMotorDir(BALLAST1_MOTOR1_PIN1, BALLAST1_MOTOR1_PIN2, dir);
  setMotorDir(BALLAST1_MOTOR2_PIN1, BALLAST1_MOTOR2_PIN2, dir);
  setMotorDir(BALLAST2_MOTOR1_PIN1, BALLAST2_MOTOR1_PIN2, dir);
  setMotorDir(BALLAST2_MOTOR2_PIN1, BALLAST2_MOTOR2_PIN2, dir);
}

void writeFinServosMicroseconds(int rudderUs, int elevatorUs) {
  const int rudder1Us = clampPulseUs(rudderUs + RUDDER_TRIM_US_1);
  const int elevator1Us = clampPulseUs(elevatorUs + ELEVATOR_TRIM_US_1);

  rudderServo.writeMicroseconds(rudder1Us);
  elevatorServo.writeMicroseconds(elevator1Us);

  if (USE_PAIRED_RUDDER) {
    const int baseRudder2Us = MIRROR_RUDDER_2 ? mirrorPulseUs(rudder1Us) : rudder1Us;
    const int rudder2Us = clampPulseUs(baseRudder2Us + RUDDER_TRIM_US_2);
    rudderServo2.writeMicroseconds(rudder2Us);
  }
  if (USE_PAIRED_ELEVATOR) {
    const int baseElevator2Us = MIRROR_ELEVATOR_2 ? mirrorPulseUs(elevator1Us) : elevator1Us;
    const int elevator2Us = clampPulseUs(baseElevator2Us + ELEVATOR_TRIM_US_2);
    elevatorServo2.writeMicroseconds(elevator2Us);
  }
}

void applyActuatorsFromCommands() {
  mainThruster.writeMicroseconds(mapPctToUs(cmdThrusterPct));
  bowThruster.writeMicroseconds(mapPctToUs(cmdBowPct));
  writeFinServosMicroseconds(mapDegToUs(cmdRudderDeg), mapDegToUs(cmdElevatorDeg));
  setBallastFromDir(cmdBallastDir);
}

void applyWatchdogFailsafe() {
  cmdThrusterPct = 0;
  cmdBowPct      = 0;
  cmdRudderDeg   = 0;
  cmdElevatorDeg = 0;
  cmdBallastDir  = -1;   // ascend on comms loss
  applyActuatorsFromCommands();
}

// ── SENSORS ───────────────────────────────────────────────────────────────

int readUltrasonicCm(uint8_t trigPin, uint8_t echoPin) {
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
  if (!imu.begin(Wire)) return false;
  imu.enableBlockDataUpdate();
  imu.setAccelDataRate(LSM6DSV16X_ODR_AT_120Hz);
  imu.setGyroDataRate(LSM6DSV16X_ODR_AT_120Hz);
  imu.setAccelFullScale(LSM6DSV16X_16g);
  imu.setGyroFullScale(LSM6DSV16X_2000dps);
  return true;
}

void readImuTelemetry(float *ax, float *ay, float *az,
                      float *gx, float *gy, float *gz) {
  *ax = *ay = *az = *gx = *gy = *gz = 0.0f;
  if (!imuReady) return;
  if (!imu.getAccel(&imuAccel) || !imu.getGyro(&imuGyro)) return;
  *ax = imuAccel.xData * MG_TO_MPS2;
  *ay = imuAccel.yData * MG_TO_MPS2;
  *az = imuAccel.zData * MG_TO_MPS2;
  *gx = imuGyro.xData  * MDPS_TO_DPS;
  *gy = imuGyro.yData  * MDPS_TO_DPS;
  *gz = imuGyro.zData  * MDPS_TO_DPS;
}

float readBatteryVoltageAvg() {
  const float vBatt = (analogRead(BATTERY_PIN) / 4095.0f) * 3.3f * BATTERY_DIVIDER_RATIO;
  batReadings[batIdx] = vBatt;
  batIdx = static_cast<uint8_t>((batIdx + 1) % BAT_AVG_N);
  if (batIdx == 0) batBufFilled = true;

  const uint8_t n = batBufFilled ? BAT_AVG_N : batIdx;
  if (n == 0) return vBatt;

  float sum = 0.0f;
  for (uint8_t i = 0; i < n; ++i) sum += batReadings[i];
  return sum / static_cast<float>(n);
}

// ── SERIAL PROTOCOL ───────────────────────────────────────────────────────

void parseCmdLine(const char *line) {
  if (strncmp(line, "CMD,", 4) != 0) return;

  int t = 0, b = 0, r = 0, e = 0, bal = 0;
  if (sscanf(line, "CMD,%d,%d,%d,%d,%d", &t, &b, &r, &e, &bal) != 5) return;

  cmdThrusterPct = clampInt(t, -100, 100);
  cmdBowPct      = clampInt(b, -100, 100);
  cmdRudderDeg   = clampInt(r, -45, 45);
  cmdElevatorDeg = clampInt(e, -45, 45);
  cmdBallastDir  = clampInt(bal, -1, 1);

  firstCmdReceived = true;
  watchdogActive   = false;
  lastCmdMillis    = millis();
  applyActuatorsFromCommands();

#if SERIAL_DEBUG
  Serial.print(F("DBG,CMD_ACK,"));
  Serial.print(cmdThrusterPct); Serial.print(',');
  Serial.print(cmdBowPct);      Serial.print(',');
  Serial.print(cmdRudderDeg);   Serial.print(',');
  Serial.print(cmdElevatorDeg); Serial.print(',');
  Serial.print(cmdBallastDir);  Serial.print('\n');
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
  float ax, ay, az, gx, gy, gz;
  readImuTelemetry(&ax, &ay, &az, &gx, &gy, &gz);

  JETSON_SERIAL.print(F("IMU,"));
  JETSON_SERIAL.print(ax, 4); JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(ay, 4); JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(az, 4); JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(gx, 4); JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(gy, 4); JETSON_SERIAL.print(',');
  JETSON_SERIAL.print(gz, 4); JETSON_SERIAL.print('\n');

  // USS protocol: top, left, right, front — only top sensor is wired
  JETSON_SERIAL.print(F("USS,"));
  JETSON_SERIAL.print(ussTopCm);
  JETSON_SERIAL.print(F(",-1,-1,-1\n"));

  JETSON_SERIAL.print(F("BAT,"));
  JETSON_SERIAL.print(readBatteryVoltageAvg(), 3);
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

// ── SETUP / LOOP ──────────────────────────────────────────────────────────

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
  if (USE_PAIRED_RUDDER)   rudderServo2.attach(RUDDER_SERVO_2_PIN);
  if (USE_PAIRED_ELEVATOR) elevatorServo2.attach(ELEVATOR_SERVO_2_PIN);

  constexpr uint8_t ballastPins[] = {
    BALLAST1_MOTOR1_PIN1, BALLAST1_MOTOR1_PIN2,
    BALLAST1_MOTOR2_PIN1, BALLAST1_MOTOR2_PIN2,
    BALLAST2_MOTOR1_PIN1, BALLAST2_MOTOR1_PIN2,
    BALLAST2_MOTOR2_PIN1, BALLAST2_MOTOR2_PIN2,
  };
  for (auto p : ballastPins) {
    pinMode(p, OUTPUT);
    digitalWrite(p, LOW);
  }

  mainThruster.writeMicroseconds(ESC_NEUTRAL_US);
  bowThruster.writeMicroseconds(ESC_NEUTRAL_US);
  writeFinServosMicroseconds(ESC_NEUTRAL_US, ESC_NEUTRAL_US);
  delay(ESC_ARM_HOLD_MS);

  if (TEST_MAIN_THRUSTER_ON_BOOT) {
    mainThruster.writeMicroseconds(mapPctToUs(TEST_MAIN_THRUSTER_PCT));
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
  imuReady      = tryInitImu();
  hasDepthSensor = depthSensor.init();
  if (hasDepthSensor) depthSensor.setFluidDensity(997);

  lastCmdMillis       = millis();
  lastTelemetryMillis = millis();
}

void loop() {
  const unsigned long now = millis();

  pumpSerialRx();

  if (firstCmdReceived && !watchdogActive &&
      (now - lastCmdMillis) > WATCHDOG_TIMEOUT_MS) {
    applyWatchdogFailsafe();
    watchdogActive = true;
#if SERIAL_DEBUG
    Serial.println(F("DBG,WD"));
#endif
  }

  if (now - lastTelemetryMillis >= TELEMETRY_PERIOD_MS) {
    lastTelemetryMillis = now;
    ussTopCm = readUltrasonicCm(ULTRASONIC_TRIG_PIN, ULTRASONIC_ECHO_PIN);
    sendTelemetryBlock();
  }
}
