// Arduino IDE: checkit 폴더 안의 checkit.ino로 열고 UNO에 업로드하세요.
// LCD가 없다면 0. 이 경우 LiquidCrystal_I2C 라이브러리도 필요 없습니다.
#define USE_LCD 1
#include <Servo.h>
#if USE_LCD
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
LiquidCrystal_I2C lcd(0x27, 16, 2);
bool lcdPresent = false;
#endif

const byte START_PIN = 2, CHECK_PIN = 3, SERVO_PIN = 5;
const byte GREEN_PIN = 6, RED_PIN = 7, BUZZER_PIN = 8;
const byte SERVO_MISSING_ANGLE = 150, SERVO_CENTER_ANGLE = 90, SERVO_READY_ANGLE = 30;
Servo signServo;
enum State { IDLE, SCANNING, MISSING, READY, ERROR_STATE, EMPTY_STATE };
State state = IDLE;
void setState(State next, byte missingCount = 0, bool updateLCD = true);
char lineBuffer[80];
byte lineLength = 0;
bool overflowed = false;
bool lastRead[2] = {HIGH, HIGH};
bool stableRead[2] = {HIGH, HIGH};
unsigned long changedAt[2] = {0, 0};

void showLines(const char* first, const char* second) {
#if USE_LCD
  static char previous[2][17] = {{0}, {0}};
  if (!lcdPresent) return;
  const char* lines[] = {first, second};
  for (byte row = 0; row < 2; row++) {
    if (strcmp(previous[row], lines[row]) == 0) continue;
    lcd.setCursor(0, row);
    byte length = strlen(lines[row]);
    for (byte col = 0; col < 16; col++) lcd.write(col < length ? lines[row][col] : ' ');
    strncpy(previous[row], lines[row], 16);
    previous[row][16] = '\0';
  }
#endif
}

void setState(State next, byte missingCount, bool updateLCD) {
  bool enteringMissing = (state != MISSING && next == MISSING);
  state = next;
  digitalWrite(GREEN_PIN, state == READY);
  digitalWrite(RED_PIN, state == MISSING);
  byte servoAngle = state == READY ? SERVO_READY_ANGLE :
                    state == SCANNING || state == MISSING ? SERVO_MISSING_ANGLE :
                    SERVO_CENTER_ANGLE;
  signServo.write(servoAngle);
  noTone(BUZZER_PIN);
  if (enteringMissing) tone(BUZZER_PIN, 1500, 250);
  if (updateLCD) {
    const char* labels[] = {"IDLE / CHECK", "SCANNING", "MISSING", "READY!", "ERROR", "NO ITEMS"};
    char detail[17] = "";
    if (state == MISSING) snprintf(detail, sizeof(detail), "Unconfirmed: %u", missingCount);
    if (state == READY) strcpy(detail, "ALL CHECKED");
    showLines(labels[state], detail);
  }
}

bool validLabel(const char* value) {
  if (strlen(value) > 16) return false;
  for (byte i = 0; value[i]; i++) {
    char c = value[i];
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
          (c >= '0' && c <= '9') || c == ' ' || c == '_' || c == '-')) return false;
  }
  return true;
}

bool processDisplay(const char* command) {
  char buffer[80];
  strncpy(buffer, command + 8, sizeof(buffer) - 1);
  buffer[sizeof(buffer) - 1] = '\0';
  char* fields[4] = {buffer, NULL, NULL, NULL};
  for (byte i = 0; i < 3; i++) {
    char* separator = strchr(fields[i], ',');
    if (!separator) return false;
    *separator = '\0'; fields[i + 1] = separator + 1;
  }
  if (strchr(fields[3], ',')) return false;
  byte digits = strlen(fields[0]);
  if (digits < 1 || digits > 10) return false;
  for (byte i = 0; i < digits; i++) if (fields[0][i] < '0' || fields[0][i] > '9') return false;
  State next;
  if (strcmp(fields[1], "SCAN") == 0) next = SCANNING;
  else if (strcmp(fields[1], "MISSING") == 0) next = MISSING;
  else if (strcmp(fields[1], "READY") == 0) next = READY;
  else if (strcmp(fields[1], "EMPTY") == 0) next = EMPTY_STATE;
  else if (strcmp(fields[1], "RESET") == 0) next = IDLE;
  else if (strcmp(fields[1], "ERROR") == 0) next = ERROR_STATE;
  else return false;
  if (next == READY) {
    if (strcmp(fields[2], "READY!") != 0 || strcmp(fields[3], "ALL CHECKED") != 0) return false;
  } else {
    if (!validLabel(fields[2]) || !validLabel(fields[3])) return false;
    if (next == EMPTY_STATE && (strcmp(fields[2], "NO ITEMS") != 0 || fields[3][0])) return false;
  }
  // 모든 필드 검증 후에 상태와 화면을 함께 적용한다.
  setState(next, 0, false);
  showLines(fields[2], fields[3]);
  Serial.print(F("ACK,DISPLAY,")); Serial.println(fields[0]);
  return true;
}

void processCommand(const char* command) {
  if (strncmp(command, "DISPLAY,", 8) == 0) {
    if (!processDisplay(command)) Serial.println(F("ERR,COMMAND"));
  } else if (strcmp(command, "SCAN") == 0) {
    setState(SCANNING); Serial.println(F("ACK,SCAN"));
  } else if (strcmp(command, "READY") == 0) {
    setState(READY); Serial.println(F("ACK,READY"));
  } else if (strcmp(command, "ERROR") == 0) {
    setState(ERROR_STATE); Serial.println(F("ACK,ERROR"));
  } else if (strcmp(command, "RESET") == 0) {
    setState(IDLE); Serial.println(F("ACK,RESET"));
  } else if (strncmp(command, "MISSING,", 8) == 0) {
    const char* number = command + 8;
    bool valid = strlen(number) >= 1 && strlen(number) <= 2;
    for (byte i = 0; number[i]; i++) {
      if (number[i] < '0' || number[i] > '9') valid = false;
    }
    int count = atoi(number);
    if (valid && count >= 1 && count <= 99) {
      setState(MISSING, count); Serial.println(F("ACK,MISSING"));
    } else Serial.println(F("ERR,COMMAND"));
  } else Serial.println(F("ERR,COMMAND"));
}

void setup() {
  pinMode(START_PIN, INPUT_PULLUP);
  pinMode(CHECK_PIN, INPUT_PULLUP);
  pinMode(GREEN_PIN, OUTPUT);
  pinMode(RED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  signServo.attach(SERVO_PIN);
  Serial.begin(115200);
#if USE_LCD
  Wire.begin();
  Wire.beginTransmission(0x27);
  lcdPresent = (Wire.endTransmission() == 0);
  if (lcdPresent) { lcd.init(); lcd.backlight(); }
#endif
  setState(IDLE);
}

void loop() {
  // delay()를 쓰지 않아 버튼 입력과 시리얼 수신을 계속 처리한다.
  if (state == SCANNING || state == ERROR_STATE) {
    digitalWrite(RED_PIN, (millis() / 350) % 2);
  }
  for (byte i = 0; i < 2; i++) {
    bool value = digitalRead(i == 0 ? START_PIN : CHECK_PIN);
    if (value != lastRead[i]) { changedAt[i] = millis(); lastRead[i] = value; }
    if (millis() - changedAt[i] >= 40 && value != stableRead[i]) {
      stableRead[i] = value;
      if (value == LOW) Serial.println(i == 0 ? F("START") : F("CHECK"));
    }
  }
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      if (overflowed) Serial.println(F("ERR,OVERFLOW"));
      else { lineBuffer[lineLength] = '\0'; processCommand(lineBuffer); }
      lineLength = 0; overflowed = false;
    } else if (!overflowed) {
      if (lineLength < sizeof(lineBuffer) - 1) lineBuffer[lineLength++] = ch;
      else overflowed = true;
    }
  }
}
