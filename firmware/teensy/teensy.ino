/**
 * Albacore — single Teensy 4.1 control + sensing firmware.
 * Protocol matches jetson/comms/protocol.py (ASCII lines, newline-terminated).
 *
 * Build: Teensy 4.1, Arduino framework, USB Serial.
 * Libraries: Servo, Wire, IntervalTimer, SparkFun_LSM6DSV16X, MS5837 (BlueRobotics).
 */

#include <Arduino.h>
#include <IntervalTimer.h>
#include <MS5837.h>
#include <Servo.h>
#include <SparkFun_LSM6DSV16X.h>
#include <Wire.h>
#include <cstring>
#include <cstdio>

// ── PIN ASSIGNMENTS (confirm with EE) ─────────────────────────────────────
constexpr uint8_t MAIN_THRUSTER_PIN = 2;
constexpr uint8_t BOW_THRUSTER_PIN = 3;
constexpr uint8_t RUDDER_SERVO_PIN = 4;
constexpr uint8_t RUDDER_SERVO_2_PIN = 5;
constexpr uint8_t ELEVATOR_SERVO_PIN = 6;
constexpr uint8_t ELEVATOR_SERVO_2_PIN = 8;
constexpr uint8_t BALLAST_IN1 = 9;
constexpr uint8_t BALLAST_IN2 = 10;

// Hydrophones (conflict-free defaults; A1/A4 avoided — see spec)
constexpr uint8_t PIEZO_PIN_0 = A0;
constexpr uint8_t PIEZO_PIN_1 = A6;
constexpr uint8_t PIEZO_PIN_2 = A2;
constexpr uint8_t PIEZO_PIN_3 = A3;
constexpr uint8_t BATTERY_PIN = A8;

// Paired fins: set true to drive RUDDER_SERVO_2 / ELEVATOR_SERVO_2 with same µs
constexpr bool USE_PAIRED_RUDDER = false;
constexpr bool USE_PAIRED_ELEVATOR = false;

// Timing
// Must match jetson/config.py CONTROL_BAUD (Teensy USB serial ignores baud; set for host tools).
constexpr unsigned long SERIAL_BAUD = 1000000;
constexpr unsigned long TELEMETRY_PERIOD_MS = 50;
constexpr unsigned long WATCHDOG_TIMEOUT_MS = 500;
constexpr unsigned long ESC_ARM_HOLD_MS = 2000;

constexpr int ESC_NEUTRAL_US = 1500;
constexpr int ESC_MIN_US = 1000;
constexpr int ESC_MAX_US = 2000;

// Audio: 5 kHz ISR, ring buffer >= 500 samples
constexpr uint32_t AUDIO_ISR_PERIOD_US = 200;
constexpr uint16_t AUDIO_BUFFER_SIZE = 512;
constexpr uint16_t AUD_LINES_PER_LOOP_MAX = 50;
constexpr int SERIAL_WRITE_HEADROOM = 1000;

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

volatile uint16_t audioBuffer[AUDIO_BUFFER_SIZE][4];
volatile uint16_t audioWriteIdx = 0;
volatile uint16_t audioReadIdx = 0;  // main loop owns read index
volatile bool audioOverrun = false;  // ISR sets when writer laps reader

IntervalTimer audioTimer;

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
void audioIsr();

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

void drainUltrasonic(HardwareSerial &port, int *distanceCm) {
  while (port.available() >= 4) {
    int h = port.peek();
    if (h != 0xFF) {
      port.read();
      continue;
    }
    uint8_t header = static_cast<uint8_t>(port.read());
    (void)header;
    uint8_t high = static_cast<uint8_t>(port.read());
    uint8_t low = static_cast<uint8_t>(port.read());
    uint8_t checksum = static_cast<uint8_t>(port.read());
    if ((((0xFFU + high + low) & 0xFFU) == checksum)) {
      uint16_t mm = (static_cast<uint16_t>(high) << 8) | low;
      *distanceCm = static_cast<int>(mm / 10);
    }
  }
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
}

void pumpSerialRx() {
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
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

  Serial.print(F("IMU,"));
  Serial.print(ax, 4);
  Serial.print(',');
  Serial.print(ay, 4);
  Serial.print(',');
  Serial.print(az, 4);
  Serial.print(',');
  Serial.print(gx, 4);
  Serial.print(',');
  Serial.print(gy, 4);
  Serial.print(',');
  Serial.print(gz, 4);
  Serial.print('\n');

  Serial.print(F("USS,"));
  Serial.print(ussTopCm);
  Serial.print(',');
  Serial.print(ussLeftCm);
  Serial.print(',');
  Serial.print(ussRightCm);
  Serial.print(',');
  Serial.print(ussFrontCm);
  Serial.print('\n');

  const float batV = readBatteryVoltageAvg();
  Serial.print(F("BAT,"));
  Serial.print(batV, 3);
  Serial.print('\n');

  float depthM = 0.0f;
  if (hasDepthSensor) {
    depthSensor.read();
    depthM = depthSensor.depth();
  }
  Serial.print(F("DEP,"));
  Serial.print(depthM, 3);
  Serial.print('\n');
}

void flushAudioLines() {
  for (uint16_t n = 0; n < AUD_LINES_PER_LOOP_MAX; ++n) {
    if (Serial.availableForWrite() < SERIAL_WRITE_HEADROOM) {
      break;
    }

    noInterrupts();
    const uint16_t w = audioWriteIdx;
    if (audioOverrun) {
      // Writer lapped reader: drop stale backlog, keep most recent samples.
      audioReadIdx = static_cast<uint16_t>((w + 1) % AUDIO_BUFFER_SIZE);
      audioOverrun = false;
    }
    const uint16_t r = audioReadIdx;
    if (r == w) {
      interrupts();
      break;
    }
    const uint16_t c0 = audioBuffer[r][0];
    const uint16_t c1 = audioBuffer[r][1];
    const uint16_t c2 = audioBuffer[r][2];
    const uint16_t c3 = audioBuffer[r][3];
    audioReadIdx = static_cast<uint16_t>((r + 1) % AUDIO_BUFFER_SIZE);
    interrupts();

    Serial.print(F("AUD,"));
    Serial.print(c0);
    Serial.print(',');
    Serial.print(c1);
    Serial.print(',');
    Serial.print(c2);
    Serial.print(',');
    Serial.print(c3);
    Serial.print('\n');
  }
}

void audioIsr() {
  const uint16_t w = audioWriteIdx;
  const uint16_t next = static_cast<uint16_t>((w + 1) % AUDIO_BUFFER_SIZE);
  if (next == audioReadIdx) {
    audioOverrun = true;
  }
  audioBuffer[w][0] = static_cast<uint16_t>(analogRead(PIEZO_PIN_0));
  audioBuffer[w][1] = static_cast<uint16_t>(analogRead(PIEZO_PIN_1));
  audioBuffer[w][2] = static_cast<uint16_t>(analogRead(PIEZO_PIN_2));
  audioBuffer[w][3] = static_cast<uint16_t>(analogRead(PIEZO_PIN_3));
  audioWriteIdx = next;
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  analogReadResolution(12);
  pinMode(BATTERY_PIN, INPUT);

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

  Serial1.begin(9600);
  Serial2.begin(9600);
  Serial3.begin(9600);
  Serial4.begin(9600);

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

  audioTimer.begin(audioIsr, AUDIO_ISR_PERIOD_US);

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
  }

  drainUltrasonic(Serial1, &ussTopCm);
  drainUltrasonic(Serial2, &ussLeftCm);
  drainUltrasonic(Serial3, &ussRightCm);
  drainUltrasonic(Serial4, &ussFrontCm);

  if (now - lastTelemetryMillis >= TELEMETRY_PERIOD_MS) {
    lastTelemetryMillis = now;
    sendTelemetryBlock();
  }

  flushAudioLines();
}
