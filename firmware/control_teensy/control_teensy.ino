#include <Arduino.h>
#include <Servo.h>
#include <Wire.h>
#include <SparkFunLSM6DSV16X.h>
#include <NewPing.h>
#include <MS5837.h>

// Pin mapping (adjust to your actual harness)
static const int THRUSTER_PIN = 2;
static const int RUDDER_PIN = 3;
static const int ELEVATOR_PIN = 4;
static const int FIN3_PIN = 5;
static const int FIN4_PIN = 6;
static const int BALLAST_IN1_PIN = 7;
static const int BALLAST_IN2_PIN = 8;
static const int BATTERY_ADC_PIN = A9;

static const int US_TOP_TRIG = 22;
static const int US_TOP_ECHO = 23;
static const int US_LEFT_TRIG = 24;
static const int US_LEFT_ECHO = 25;
static const int US_RIGHT_TRIG = 26;
static const int US_RIGHT_ECHO = 27;
static const int US_FRONT_TRIG = 28;
static const int US_FRONT_ECHO = 29;
static const int US_MAX_CM = 300;

static const int ESC_NEUTRAL_US = 1500;
static const int ESC_MIN_US = 1000;
static const int ESC_MAX_US = 2000;
static const unsigned long TELEMETRY_PERIOD_MS = 50;
static const unsigned long WATCHDOG_TIMEOUT_MS = 500;

Servo thrusterEsc;
Servo rudderServo;
Servo elevatorServo;
Servo fin3Servo;
Servo fin4Servo;

SparkFunLSM6DSV16X imu;
MS5837 depthSensor;
bool imuReady = false;
bool depthReady = false;

NewPing usTop(US_TOP_TRIG, US_TOP_ECHO, US_MAX_CM);
NewPing usLeft(US_LEFT_TRIG, US_LEFT_ECHO, US_MAX_CM);
NewPing usRight(US_RIGHT_TRIG, US_RIGHT_ECHO, US_MAX_CM);
NewPing usFront(US_FRONT_TRIG, US_FRONT_ECHO, US_MAX_CM);

int cmdThrusterPct = 0;
int cmdRudderDeg = 0;
int cmdElevatorDeg = 0;
int cmdBallastDir = 0;
unsigned long lastCmdMillis = 0;
unsigned long lastTelemetryMillis = 0;
String rxLine;

static int clampInt(int value, int lo, int hi) {
  if (value < lo) return lo;
  if (value > hi) return hi;
  return value;
}

static int mapThrusterPctToUs(int pct) {
  pct = clampInt(pct, -100, 100);
  return ESC_NEUTRAL_US + (pct * 5);
}

static int mapControlDegToServoAngle(int deg) {
  deg = clampInt(deg, -45, 45);
  return map(deg, -45, 45, 0, 180);
}

void setBallastMotor(int dir) {
  dir = clampInt(dir, -1, 1);
  if (dir < 0) {
    digitalWrite(BALLAST_IN1_PIN, HIGH);
    digitalWrite(BALLAST_IN2_PIN, LOW);
  } else if (dir > 0) {
    digitalWrite(BALLAST_IN1_PIN, LOW);
    digitalWrite(BALLAST_IN2_PIN, HIGH);
  } else {
    digitalWrite(BALLAST_IN1_PIN, LOW);
    digitalWrite(BALLAST_IN2_PIN, LOW);
  }
}

void applyActuators() {
  thrusterEsc.writeMicroseconds(clampInt(mapThrusterPctToUs(cmdThrusterPct), ESC_MIN_US, ESC_MAX_US));
  rudderServo.write(mapControlDegToServoAngle(cmdRudderDeg));
  elevatorServo.write(mapControlDegToServoAngle(cmdElevatorDeg));
  fin3Servo.write(90);
  fin4Servo.write(90);
  setBallastMotor(cmdBallastDir);
}

void applyFailsafe() {
  cmdThrusterPct = 0;
  cmdBallastDir = -1;
  applyActuators();
}

void parseCmdLine(const String &line) {
  if (!line.startsWith("CMD,")) return;

  int fields[4] = {0, 0, 0, 0};
  int start = 4;
  for (int i = 0; i < 4; ++i) {
    int comma = line.indexOf(',', start);
    if (comma < 0) comma = line.length();
    fields[i] = line.substring(start, comma).toInt();
    start = comma + 1;
  }

  cmdThrusterPct = clampInt(fields[0], -100, 100);
  cmdRudderDeg = clampInt(fields[1], -45, 45);
  cmdElevatorDeg = clampInt(fields[2], -45, 45);
  cmdBallastDir = clampInt(fields[3], -1, 1);
  lastCmdMillis = millis();
  applyActuators();
}

void pumpSerialRx() {
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      parseCmdLine(rxLine);
      rxLine = "";
    } else if (c != '\r') {
      rxLine += c;
      if (rxLine.length() > 120) rxLine = "";
    }
  }
}

float readBatteryVoltage() {
  int raw = analogRead(BATTERY_ADC_PIN);
  float vAdc = (raw / 1023.0f) * 3.3f;
  float dividerGain = 4.0f;  // tune based on resistor divider ratio
  return vAdc * dividerGain;
}

void sendTelemetry() {
  float ax = 0.0f, ay = 0.0f, az = 0.0f, gx = 0.0f, gy = 0.0f, gz = 0.0f;
  if (imuReady) {
    imu.getAccel(&ax, &ay, &az);
    imu.getGyro(&gx, &gy, &gz);
  }
  int topCm = usTop.ping_cm();
  int leftCm = usLeft.ping_cm();
  int rightCm = usRight.ping_cm();
  int frontCm = usFront.ping_cm();
  float batV = readBatteryVoltage();
  float depthM = 0.0f;
  if (depthReady) {
    depthSensor.read();
    depthM = depthSensor.depth();
  }

  Serial.printf("IMU,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f\n", ax, ay, az, gx, gy, gz);
  Serial.printf("USS,%d,%d,%d,%d\n", topCm, leftCm, rightCm, frontCm);
  Serial.printf("BAT,%.3f\n", batV);
  Serial.printf("DEP,%.3f\n", depthM);
}

void setup() {
  Serial.begin(115200);
  Wire.begin();

  thrusterEsc.attach(THRUSTER_PIN);
  rudderServo.attach(RUDDER_PIN);
  elevatorServo.attach(ELEVATOR_PIN);
  fin3Servo.attach(FIN3_PIN);
  fin4Servo.attach(FIN4_PIN);

  pinMode(BALLAST_IN1_PIN, OUTPUT);
  pinMode(BALLAST_IN2_PIN, OUTPUT);
  pinMode(BATTERY_ADC_PIN, INPUT);

  applyFailsafe();
  lastCmdMillis = millis();
  lastTelemetryMillis = millis();

  if (imu.begin(Wire) == true) {
    imuReady = true;
  }

  if (depthSensor.init()) {
    depthReady = true;
    depthSensor.setFluidDensity(997);
  }
}

void loop() {
  pumpSerialRx();

  unsigned long now = millis();
  if ((now - lastCmdMillis) > WATCHDOG_TIMEOUT_MS) {
    applyFailsafe();
  }

  if ((now - lastTelemetryMillis) >= TELEMETRY_PERIOD_MS) {
    lastTelemetryMillis = now;
    sendTelemetry();
  }
}
