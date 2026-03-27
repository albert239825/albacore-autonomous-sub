#include <Arduino.h>

// Set to 1 to use binary framing instead of ASCII lines.
#define USE_BINARY_FRAMING 0

static const int PIN_CH0 = A0;
static const int PIN_CH1 = A1;
static const int PIN_CH2 = A2;
static const int PIN_CH3 = A3;

static const uint32_t SAMPLE_RATE_HZ = 20000;
static const uint32_t SAMPLE_PERIOD_US = 1000000UL / SAMPLE_RATE_HZ;
static const uint16_t RING_CAPACITY = 4096;

struct AudioSample {
  uint16_t ch0;
  uint16_t ch1;
  uint16_t ch2;
  uint16_t ch3;
};

volatile AudioSample ringBuffer[RING_CAPACITY];
volatile uint16_t writeIdx = 0;
volatile uint16_t readIdx = 0;
volatile uint32_t droppedSamples = 0;
IntervalTimer sampleTimer;

void sampleISR() {
  uint16_t nextWrite = (writeIdx + 1) % RING_CAPACITY;
  if (nextWrite == readIdx) {
    droppedSamples++;
    return;
  }

  AudioSample s;
  s.ch0 = analogRead(PIN_CH0);
  s.ch1 = analogRead(PIN_CH1);
  s.ch2 = analogRead(PIN_CH2);
  s.ch3 = analogRead(PIN_CH3);

  ringBuffer[writeIdx] = s;
  writeIdx = nextWrite;
}

bool popSample(AudioSample *out) {
  noInterrupts();
  if (readIdx == writeIdx) {
    interrupts();
    return false;
  }
  *out = ringBuffer[readIdx];
  readIdx = (readIdx + 1) % RING_CAPACITY;
  interrupts();
  return true;
}

void setup() {
  Serial.begin(2000000);
  analogReadResolution(12);
  pinMode(PIN_CH0, INPUT);
  pinMode(PIN_CH1, INPUT);
  pinMode(PIN_CH2, INPUT);
  pinMode(PIN_CH3, INPUT);
  sampleTimer.begin(sampleISR, SAMPLE_PERIOD_US);
}

void loop() {
  AudioSample s;
  while (popSample(&s)) {
#if USE_BINARY_FRAMING
    const uint8_t header[1] = {0xA5};
    const uint8_t footer[1] = {0x5A};
    Serial.write(header, 1);
    Serial.write(reinterpret_cast<const uint8_t *>(&s), sizeof(AudioSample));
    Serial.write(footer, 1);
#else
    Serial.printf("AUD,%u,%u,%u,%u\n", s.ch0, s.ch1, s.ch2, s.ch3);
#endif
  }

  static unsigned long lastDiagMs = 0;
  unsigned long now = millis();
  if (now - lastDiagMs >= 1000) {
    lastDiagMs = now;
    if (droppedSamples > 0) {
      Serial.printf("DBG,DROPPED,%lu\n", droppedSamples);
    }
  }
}
