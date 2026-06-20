# TV Cast 컨트롤러

라즈베리파이(또는 PC)에서 `catt` 로 Chromecast/스마트TV 에 YouTube 영상을 캐스팅하고,
시간 예약(cron)으로 자동 재생/종료까지 관리하는 도구입니다.

## 설치

```bash
pip install catt          # 필수 (캐스팅 엔진)
pip install rich          # 선택 (컬러 UI)
pip install questionary   # 선택 (화살표 ↑↓ 메뉴)
```
> 최신 라즈베리파이OS(Bookworm)에서 `externally-managed-environment` 오류가 나면
> `pip install --break-system-packages catt rich questionary` 로 설치하세요.

## 실행

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

## config.json 구조

| 키 | 설명 |
|----|------|
| `device_name` | `catt scan` 에 나오는 기기 이름 (우선 사용) |
| `device_ip` | 이름이 비어있으면 이 IP 로 연결 |
| `settings.catt_path` | catt 실행파일 절대경로 (cron 안전을 위해 권장) |
| `settings.volume` | (선택) 기본 볼륨 `0~100`. 재생 시 자동 적용. 없으면 TV 볼륨 그대로 |
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
