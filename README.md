# TV Cast 컨트롤러

`catt` 로 Chromecast/스마트TV 에 YouTube 영상을 캐스팅하고,
시간 예약(cron)으로 자동 재생/종료까지 관리하는 도구입니다.

## 지원 환경

| 환경 | 재생·볼륨·메뉴 | 자동 예약(cron) |
|------|:--:|:--:|
| 리눅스 (라즈베리파이OS · 우분투 · 데비안 · 페도라 등) | ✅ | ✅ |
| macOS | ✅ | ✅ |
| Windows | ✅ | ❌ (cron 없음 — 재생만 가능) |

Python 3 와 같은 네트워크의 Cast 기기만 있으면 됩니다. `catt` 경로는 메뉴의
`⚙️ 설정 → catt 경로 자동 찾기` 가 OS 에 맞게 자동으로 잡아줍니다.

## 설치

```bash
pip install catt          # 필수 (캐스팅 엔진)
pip install rich          # 선택 (컬러 UI)
pip install questionary   # 선택 (화살표 ↑↓ 메뉴)
```
> 데비안 계열(우분투 · 라즈베리파이OS Bookworm 등)에서 `externally-managed-environment`
> 오류가 나면 `pip install --break-system-packages catt rich questionary` 로 설치하세요.

## 실행 — ① 터미널 메뉴

```bash
python3 tvcast.py
```

메뉴 구조:

```
📺 TV Cast — 기기: TV   ⏰ 자동예약 N건 켜짐
   ▶  ⭐ 즐겨찾기 영상      ← 한 번에 재생
   ▶  재생할 영상 고르기
   ■  재생 종료 / ℹ 현재 상태
   ⏰ 자동 예약            ← 예약 추가·켜기/끄기·삭제 (cron 자동 반영)
   🎬 영상 목록 관리        ← 영상 추가·⭐즐겨찾기·이름변경·삭제
   ⚙️  설정                ← 기기 선택 · catt 경로 · cron 확인
   🚪 종료
```

**핵심:** 자동 예약을 **켜거나 끄면 그 즉시 cron 에 반영**됩니다. 따로 "등록" 단계가 없습니다.
메뉴에서 바꾼 내용은 모두 `config.json` 에 **바로 저장**됩니다.

## 실행 — ② 웹 리모컨 (폰/PC 브라우저)

같은 WiFi 의 폰·PC 브라우저에서 버튼으로 제어합니다. 터미널 메뉴와 **같은 `config.json` 을 공유**해서,
웹에서 바꾼 영상·예약·볼륨이 cron 과 CLI 에도 그대로 반영됩니다.

```bash
pip install flask
python3 tvcast.py web            # 또는  python3 tvcast_web.py
# 폰 브라우저에서  http://<이 기기 IP>:8888  접속
#   (이 기기 IP 확인:  hostname -I)
```

터미널 메뉴에서도 **`🌐 웹 리모컨 켜기`** 를 고르면 바로 실행됩니다 (Ctrl-C 로 종료 → 메뉴 복귀).

**포트 변경**: 메뉴의 `⚙️ 설정 → 🌐 웹 포트 변경`, 또는 `config.json` 의 `settings.web_port` (기본 8888).
일시적으로는 `TVCAST_PORT=9000 python3 tvcast.py web` 환경변수도 가능 (환경변수가 우선).

- 즐겨찾기 영상 탭 → 즉시 재생 / 정지 / 상태
- 볼륨 슬라이더 (드래그하면 기본 볼륨 저장 + 재생 중이면 바로 적용)
- 자동 예약 추가·켜기/끄기·삭제 (→ cron 자동 반영)
- 기기 검색·선택, catt 경로 자동 찾기

> ⚠️ 기본적으로 **인증이 없습니다** — 같은 네트워크의 누구나 제어할 수 있어요. 집 내부망 용도로만 쓰세요.

**항상 켜두기 (systemd, 리눅스):** `/etc/systemd/system/tvcast-web.service`
```ini
[Unit]
Description=TV Cast Web
After=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/tvcast
ExecStart=/usr/bin/python3 /home/pi/tvcast/tvcast_web.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now tvcast-web      # 부팅 시 자동 실행
```

## config.json 구조

| 키 | 설명 |
|----|------|
| `device_name` | `catt scan` 에 나오는 기기 이름 (우선 사용) |
| `device_ip` | 이름이 비어있으면 이 IP 로 연결 |
| `settings.catt_path` | catt 실행파일 절대경로 (cron 안전을 위해 권장) |
| `settings.volume` | (선택) 기본 볼륨 `0~100`. 재생 시 자동 적용. 없으면 TV 볼륨 그대로 |
| `settings.web_port` | 웹 리모컨 포트 (기본 `8888`). `TVCAST_PORT` 환경변수가 있으면 그게 우선 |
| `settings.pre_stop_wait` | 재생 전 stop 후 대기 초 |
| `settings.command_timeout` | catt 명령 제한 시간(초) |
| `settings.log_path` | (선택) 동작 로그 파일 경로. 기본 `tvcast.log` |
| `videos` | `{ "1": { "name": ..., "url": ..., "fav": true } }` — `fav` 는 즐겨찾기 |
| `schedules[]` | 자동 예약 목록 |
| `schedules[].enabled` | `true` 면 cron 에 적용됨 (메뉴에서 켜기/끄기) |
| `schedules[].video` | `videos` 의 키 |
| `schedules[].start` | 켜는 시간 `HH:MM` |
| `schedules[].end` | 끄는 시간 `HH:MM` (비우면 자동 종료 없음) |
| `schedules[].days` | `평일` / `주말` / `매일` 또는 `월,화,수,목,금,토,일` |
| `schedules[].volume` | (선택) 이 예약만의 볼륨 `0~100`. 없으면 `settings.volume` 사용 |

> **볼륨**은 TV 메뉴 설정이 아니라 Cast(캐스트) 재생 볼륨입니다. 우선순위: 예약별 볼륨 → 기본 볼륨 → (둘 다 없으면) 건드리지 않음.

> 보통은 `config.json` 을 직접 편집할 필요 없이 메뉴에서 다 관리됩니다.

## cron 에서 직접 쓰는 명령

```bash
python3 tvcast.py play 1     # 1번 영상 재생 (기본 볼륨 적용)
python3 tvcast.py play 1 25  # 1번 영상 재생 + 볼륨 25
python3 tvcast.py stop       # 종료
python3 tvcast.py sync-cron  # 켜진 예약을 crontab 에 동기화
```
