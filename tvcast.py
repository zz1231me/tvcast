#!/usr/bin/env python3
"""
TV Cast 컨트롤러
- config.json 을 읽어서 동작 (메뉴에서 바꾸면 즉시 저장됨)
- 대화형 메뉴: ▶재생 / ⏰자동예약 / 🎬영상목록 / ⚙️설정
- 자동 예약(스케줄)을 켜고/끄면 cron 에 '자동으로' 반영됨 (따로 등록 단계 없음)

사용법:
  python3 tvcast.py            # 대화형 메뉴 (화살표 선택)
  python3 tvcast.py play 1     # 1번 영상 바로 재생 (cron 에서 사용)
  python3 tvcast.py play 1 25  # 1번 영상 재생 + 볼륨 25
  python3 tvcast.py stop       # 바로 종료 (cron 에서 사용)
  python3 tvcast.py status     # 상태 확인
  python3 tvcast.py scan       # 기기 검색 후 config 업데이트
  python3 tvcast.py sync-cron  # config 의 켜진 예약을 crontab 에 동기화

사전 준비:
  pip install catt          # 필수 (캐스팅 엔진)
  pip install rich          # 선택 (없으면 일반 텍스트로 동작)
  pip install questionary   # 선택 (있으면 화살표↑↓로 메뉴 선택)
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime

# config.json 은 이 스크립트와 같은 폴더에 있다고 가정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SCRIPT_PATH = os.path.abspath(__file__)

# cron 항목을 구분하기 위한 표식 (주석)
CRON_TAG = "# TVCAST_AUTO"


# ====================================================================
#  출력 계층 — rich 가 있으면 컬러 UI, 없으면 일반 텍스트로 자동 폴백
# ====================================================================

try:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    from rich import box
    _console = Console()
    HAS_RICH = True
except ImportError:
    _console = None
    HAS_RICH = False

# rich 마크업 태그( [bold] [/] 등 ) 제거용 — 폴백 모드의 ask() 프롬프트에서만 사용
_TAG_RE = re.compile(r"\[/?[a-zA-Z0-9_#\s]*\]")


def say(msg="", style=None):
    """
    한 줄 출력. 색상은 style 인자로 적용하고 마크업 해석은 끈다.
    → 사용자 입력(영상 이름/기기명 등)에 '[...]' 가 들어가도 깨지거나 크래시하지 않음.
    """
    if HAS_RICH:
        _console.print(msg, style=style, markup=False, highlight=False)
    else:
        print(msg)


def info(msg):    say(msg, style="cyan")
def success(msg): say(msg, style="green")
def warn(msg):    say(msg, style="yellow")
def error(msg):   say(msg, style="bold red")


def ask(prompt):
    """입력 프롬프트. 프롬프트는 정적 문자열이라 마크업을 허용."""
    if HAS_RICH:
        _console.print(prompt, end="")
    else:
        print(_TAG_RE.sub("", prompt), end="")
    try:
        return input().strip()
    except (EOFError, KeyboardInterrupt):
        say()
        return "q"


# ====================================================================
#  선택 계층 — questionary 가 있고 대화형 터미널이면 화살표(↑↓) 선택,
#             아니면 번호 입력으로 자동 폴백 (cron/파이프에서도 안전)
# ====================================================================

try:
    import questionary
    HAS_Q = True
except ImportError:
    HAS_Q = False


def _arrows_ok():
    """화살표 선택이 가능한 환경인지 (questionary 설치 + 대화형 TTY)"""
    return HAS_Q and sys.stdin.isatty() and sys.stdout.isatty()


def select_menu(title, options):
    """
    메뉴에서 항목 하나를 고른다.
      options : [(label, value), ...]   value 가 None 이면 구분선(선택 불가)
      반환     : 고른 value (취소/Esc/Ctrl-C/0 이면 None)
    """
    if _arrows_ok():
        choices = []
        for label, value in options:
            if value is None:
                choices.append(questionary.Separator(label))
            else:
                choices.append(questionary.Choice(title=label, value=value))
        return questionary.select(
            title, choices=choices, qmark="📺",
            instruction="(↑↓ 이동 · Enter 선택 · Ctrl-C 취소)",
        ).ask()

    # 폴백: 번호 입력
    say("")
    info(title)
    numbered = []
    for label, value in options:
        if value is None:
            say(f"   {label}", style="dim")
        else:
            numbered.append(value)
            say(f"   {len(numbered)}) {label}")
    sel = ask("[bold]번호 (0=취소) > [/]")
    if not sel.isdigit() or not (1 <= int(sel) <= len(numbered)):
        return None
    return numbered[int(sel) - 1]


def ask_confirm(prompt, default=False):
    """예/아니오 질문. 화살표 모드면 questionary.confirm, 아니면 y/N 입력."""
    if _arrows_ok():
        return bool(questionary.confirm(prompt, default=default, qmark="❓").ask())
    suffix = "(Y/n)" if default else "(y/N)"
    r = ask(f"[bold]{prompt} {suffix} > [/]").lower()
    if r in ("", "q"):
        return default if r == "" else False
    return r in ("y", "yes")


def ask_text(prompt):
    """자유 입력. 화살표 모드면 questionary.text, 아니면 일반 input. 취소 시 빈 문자열."""
    if _arrows_ok():
        r = questionary.text(prompt, qmark="✏").ask()
        return (r or "").strip()
    return ask(f"[bold]{prompt} > [/]")


def parse_volume(raw):
    """문자열을 0~100 정수 볼륨으로 변환. 빈값/범위밖/숫자아님이면 None."""
    raw = str(raw).strip()
    if not raw.isdigit():
        return None
    v = int(raw)
    return v if 0 <= v <= 100 else None


def clear_screen():
    """화면을 깨끗이 지운다 (대화형 터미널에서만; cron/파이프에선 아무것도 안 함)."""
    if sys.stdout.isatty():
        # 보이는 화면 + 스크롤백까지 지우고 커서를 맨 위로
        print("\033[3J\033[2J\033[H", end="", flush=True)


def pause():
    """작업 결과를 읽을 시간을 준 뒤 Enter 로 메뉴 복귀 (대화형 터미널에서만)."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            input("\n  ⏎  Enter 를 누르면 메뉴로 돌아갑니다... ")
        except (EOFError, KeyboardInterrupt):
            pass


# ====================================================================
#  설정 로드 / 저장 / 로그
# ====================================================================

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        error(f"설정 파일이 없습니다: {CONFIG_PATH}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        error(f"config.json 형식이 잘못되었습니다: {e}")
        sys.exit(1)


def save_config(cfg):
    """변경된 설정을 config.json 에 다시 저장 (키 순서·_help 유지)"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return True
    except OSError as e:
        error(f"config.json 저장 실패: {e}")
        return False


def log_event(cfg, msg):
    """동작 기록을 로그 파일에 남김 (cron 자동실행 실패 추적용)"""
    path = get_setting(cfg, "log_path", os.path.join(BASE_DIR, "tvcast.log"))
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass  # 로그 실패가 본 동작을 막으면 안 됨


# ====================================================================
#  설정 읽기 헬퍼
# ====================================================================

def get_device(cfg):
    """device_name 이 있으면 이름, 없으면 device_ip 를 반환"""
    name = str(cfg.get("device_name", "")).strip()
    ip = str(cfg.get("device_ip", "")).strip()
    if name:
        return name
    if ip:
        return ip
    error("config.json 에 device_name 또는 device_ip 가 필요합니다.")
    sys.exit(1)


def get_setting(cfg, key, default):
    """settings 에서 값 읽기 (없으면 기본값)"""
    return cfg.get("settings", {}).get(key, default)


def get_catt(cfg):
    """catt 실행 경로 반환 (config 에 지정 없으면 PATH 의 catt 사용)"""
    return get_setting(cfg, "catt_path", "catt")


# ====================================================================
#  catt 실행 — 모든 catt 호출이 이 한 곳을 거침 (중복 제거)
# ====================================================================

def run_catt(cfg, args, timeout=60, quiet=False):
    """
    catt 명령 실행. (성공여부: bool, 표준출력: str) 반환.
    quiet=True 이면 화면에 출력하지 않음 (재생 전 사전 stop 등).
    """
    catt = get_catt(cfg)
    device = get_device(cfg)
    cmd = [catt, "-d", device] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        if not quiet:  # 사전 stop(quiet) 에선 중복 출력 방지 — 이어지는 실제 명령이 알림
            error(f"catt 를 찾을 수 없습니다: {catt}")
            say("   settings.catt_path 를 확인하세요. (which catt 로 경로 확인)", style="dim")
        return False, ""
    except subprocess.TimeoutExpired:
        if not quiet:
            error("명령 시간 초과. 기기 연결을 확인하세요.")
        return False, ""

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if not quiet:
        if out:
            say(out)
        if err:
            say(err, style="dim")
    return result.returncode == 0, out


# ====================================================================
#  재생 / 종료 / 상태
# ====================================================================

def play_video(cfg, key, volume=None):
    """
    영상 재생. volume(0~100)이 주어지면 그 볼륨, 없으면 settings.volume,
    그것도 없으면 볼륨을 건드리지 않는다(TV 볼륨 그대로).
    """
    videos = cfg.get("videos", {})
    if key not in videos:
        error(f"'{key}' 번 영상이 config.json 에 없습니다.")
        return
    video = videos[key]
    url = video.get("url")
    if not url:
        error(f"'{key}' 번 영상에 url 이 없습니다.")
        log_event(cfg, f"PLAY FAIL 영상={key} (url 없음)")
        return
    device = get_device(cfg)
    wait = get_setting(cfg, "pre_stop_wait", 3)
    timeout = get_setting(cfg, "command_timeout", 60)

    # 적용할 볼륨 결정: 인자 > settings.volume > 없음
    vol = volume if volume is not None else get_setting(cfg, "volume", None)
    if vol is not None:
        vol = parse_volume(vol)  # 잘못된 값은 None 처리(건너뜀)

    # 기존 캐스트 세션이 남아있으면 screen_id 충돌로 400 에러가 난다.
    # 재생 전에 먼저 stop 으로 세션을 정리한다.
    run_catt(cfg, ["stop"], timeout=timeout, quiet=True)
    time.sleep(wait)

    vname = video.get("name", key)
    info(f"▶  '{device}' 에서 재생: {vname}")
    ok, _ = run_catt(cfg, ["cast", url], timeout=timeout)
    if ok:
        success("   재생 시작됨")
        if vol is not None:
            if run_catt(cfg, ["volume", str(vol)], timeout=timeout, quiet=True)[0]:
                info(f"   🔊 볼륨 {vol}")
        log_event(cfg, f"PLAY ok   기기={device} 영상={key}:{vname} 볼륨={vol}")
    else:
        log_event(cfg, f"PLAY FAIL 기기={device} 영상={key}:{vname}")


def stop_video(cfg):
    timeout = get_setting(cfg, "command_timeout", 60)
    device = get_device(cfg)
    info(f"■  '{device}' 재생 종료")
    ok, _ = run_catt(cfg, ["stop"], timeout=timeout)
    log_event(cfg, f"STOP {'ok' if ok else 'FAIL'} 기기={device}")


def show_status(cfg):
    timeout = get_setting(cfg, "command_timeout", 60)
    info(f"ℹ  '{get_device(cfg)}' 상태:")
    run_catt(cfg, ["status"], timeout=timeout)


# ====================================================================
#  기기 검색 / 선택
# ====================================================================

def scan_devices(cfg):
    """
    'catt scan' 으로 네트워크의 Cast 기기를 검색해서
    [{'ip': ..., 'name': ..., 'info': ...}, ...] 리스트로 반환.
    """
    catt = get_catt(cfg)
    info("🔎 네트워크에서 Cast 기기를 검색 중... (몇 초 걸립니다)")
    try:
        result = subprocess.run([catt, "scan"],
                                capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        error(f"catt 를 찾을 수 없습니다: {catt}  (settings.catt_path 확인)")
        return []
    except subprocess.TimeoutExpired:
        error("검색 시간 초과. 같은 네트워크(WiFi)에 있는지 확인하세요.")
        return []

    devices = []
    for line in result.stdout.splitlines():
        line = line.strip()
        # catt scan 출력 예: "10.30.30.113 - TV - Google Inc. Chromecast"
        parts = [p.strip() for p in line.split(" - ")]
        if len(parts) < 2 or "." not in parts[0]:
            continue  # 헤더("Scanning...") 등은 건너뜀
        devices.append({
            "ip": parts[0],
            "name": parts[1],
            "info": parts[2] if len(parts) >= 3 else "",
        })
    return devices


def choose_device(cfg):
    """기기를 검색해 사용자가 고르면 config.json 의 device_name/ip 를 갱신·저장"""
    devices = scan_devices(cfg)
    if not devices:
        warn("검색된 Cast 기기가 없습니다. (TV 전원/네트워크 확인)")
        return

    current = get_device(cfg)

    options = []
    for d in devices:
        mark = "  ← 현재" if d["name"] == current or d["ip"] == current else ""
        info_txt = f"  ({d['info']})" if d["info"] else ""
        options.append((f"{d['name']}  [{d['ip']}]{info_txt}{mark}", d))
    options.append(("↩  취소", "__cancel__"))

    chosen = select_menu("사용할 Cast 기기 선택", options)
    if chosen is None or chosen == "__cancel__":
        warn("취소했습니다.")
        return

    cfg["device_name"] = chosen["name"]
    cfg["device_ip"] = chosen["ip"]
    if save_config(cfg):
        success(f"✅ 기기를 '{chosen['name']}' [{chosen['ip']}] 로 설정하고 config.json 에 저장했습니다.")


# ====================================================================
#  cron 관련
# ====================================================================

def has_crontab():
    """crontab 명령이 시스템에 있는지 확인"""
    from shutil import which
    return which("crontab") is not None


def get_crontab():
    """현재 crontab 내용을 리스트로 반환 (없으면 빈 리스트)"""
    if not has_crontab():
        return []
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def set_crontab(lines):
    """crontab 을 주어진 라인들로 교체"""
    if not has_crontab():
        error("이 시스템에 'crontab' 명령이 없습니다. (리눅스/macOS 기본 포함, Windows 는 미지원)")
        return False
    content = "\n".join(lines).strip() + "\n"
    proc = subprocess.run(["crontab", "-"], input=content, text=True)
    return proc.returncode == 0


def time_to_cron(hhmm):
    """'07:00' -> ('0', '7'). 형식/범위가 잘못되면 None 반환."""
    try:
        hh, mm = str(hhmm).split(":")
        hh, mm = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return str(mm), str(hh)


# 요일 이름 -> cron 요일 번호 (0=일요일 ... 6=토요일, cron 표준)
DAY_MAP = {
    "sun": "0", "mon": "1", "tue": "2", "wed": "3",
    "thu": "4", "fri": "5", "sat": "6",
    # 한글 별칭
    "일": "0", "월": "1", "화": "2", "수": "3",
    "목": "4", "금": "5", "토": "6",
    "일요일": "0", "월요일": "1", "화요일": "2", "수요일": "3",
    "목요일": "4", "금요일": "5", "토요일": "6",
}

# 묶음 별칭 (영어 + 한글)
GROUP_EVERYDAY = {"everyday", "매일", "날마다"}
GROUP_WEEKDAY = {"weekday", "평일"}
GROUP_WEEKEND = {"weekend", "주말"}


def days_to_cron(days):
    """
    days 리스트를 cron 요일 필드 문자열로 변환
    - ['매일'] / ['everyday']        -> '*'
    - ['평일'] / ['weekday']         -> '1-5'
    - ['주말'] / ['weekend']         -> '0,6'
    - ['월','수','금']               -> '1,3,5'
    """
    if not days:
        return "*"
    lowered = [str(d).lower() for d in days]

    if any(d in GROUP_EVERYDAY for d in lowered):
        return "*"
    if any(d in GROUP_WEEKDAY for d in lowered):
        return "1-5"
    if any(d in GROUP_WEEKEND for d in lowered):
        return "0,6"

    nums = []
    for d in lowered:
        if d in DAY_MAP:
            nums.append(DAY_MAP[d])
        else:
            warn(f"[경고] 알 수 없는 요일 '{d}' 무시됨")
    if not nums:
        return "*"
    # 중복 제거 + 정렬
    nums = sorted(set(nums), key=int)
    return ",".join(nums)


def sync_cron(cfg, verbose=True):
    """
    config 의 '켜진(enabled)' 스케줄을 crontab 에 그대로 동기화한다.
    - 켜진 스케줄 → cron 에 등록
    - 켜진 스케줄이 하나도 없으면 → TVCAST cron 항목이 모두 사라짐(=자동 해제)
    반환:
      - 적용된 스케줄 설명 리스트 (0건이면 빈 리스트)
      - crontab 자체를 쓸 수 없으면 None (실패와 '0건'을 구분)
    """
    if not has_crontab():
        if verbose:
            error("이 시스템에 'crontab' 명령이 없습니다. (리눅스/macOS 기본 포함, Windows 는 미지원)")
        return None

    schedules = cfg.get("schedules", [])
    videos = cfg.get("videos", {})
    python = sys.executable or "python3"
    py_q = shlex.quote(python)
    script_q = shlex.quote(SCRIPT_PATH)

    # catt 경로가 절대경로가 아니면 cron 자동실행이 실패할 수 있다.
    catt_cfg = str(get_setting(cfg, "catt_path", "")).strip()
    if verbose and not (catt_cfg and os.path.isabs(catt_cfg)):
        warn("주의: catt 경로가 절대경로가 아니라 자동실행 때 catt 를 못 찾을 수 있어요.")
        info("   ⚙️ 설정 → 'catt 경로 자동 찾기' 로 고정하면 안전합니다.")

    # 기존 TVCAST 라인을 싹 지우고, 켜진 스케줄로 다시 채운다.
    lines = [ln for ln in get_crontab() if CRON_TAG not in ln]
    applied = []
    for sc in schedules:
        if not sc.get("enabled"):
            continue
        name = sc.get("name", "이름없음")
        vid = str(sc.get("video", "1"))
        start = str(sc.get("start", "")).strip()
        end = str(sc.get("end", "")).strip()

        if vid not in videos:
            if verbose:
                warn(f"건너뜀 - '{name}': 영상 {vid} 가 목록에 없음")
            continue
        start_cron = time_to_cron(start) if start else None
        if not start_cron:
            if verbose:
                warn(f"건너뜀 - '{name}': 켜는 시간 '{start}' 형식 오류")
            continue
        end_cron = time_to_cron(end) if end else None
        if end and not end_cron and verbose:
            warn(f"'{name}': 끄는 시간 '{end}' 형식 오류 → 끄기 생략")
        dow = days_to_cron(sc.get("days", ["everyday"]))

        # 스케줄별 볼륨이 있으면 play 명령에 인자로 실어 보낸다.
        vol = parse_volume(sc.get("volume")) if sc.get("volume") is not None else None
        play_args = f"play {vid}" + (f" {vol}" if vol is not None else "")

        mm, hh = start_cron
        lines.append(f'{mm} {hh} * * {dow} {py_q} {script_q} {play_args} {CRON_TAG}')
        if end_cron:
            mm, hh = end_cron
            lines.append(f'{mm} {hh} * * {dow} {py_q} {script_q} stop {CRON_TAG}')
        end_txt = end if end_cron else "끔없음"
        vol_txt = f", 볼륨 {vol}" if vol is not None else ""
        applied.append(f"{name}: {start}~{end_txt} (요일 {dow}, 영상 {vid}{vol_txt})")

    if not set_crontab(lines):
        if verbose:
            error("cron 동기화 실패")
        return None
    return applied


def report_cron(cfg):
    """스케줄 변경 후 cron 에 자동 반영하고 현재 상태를 한 줄로 알린다."""
    applied = sync_cron(cfg, verbose=True)
    if applied is None:
        return  # crontab 사용 불가 — 위에서 이미 오류를 알림
    if applied:
        info(f"⏰ cron 에 자동 반영됨 — 현재 {len(applied)}건 켜짐")
    else:
        info("⏰ cron 반영됨 — 현재 켜진 예약 없음")


def show_cron():
    """현재 등록된 TVCAST cron 항목 표시"""
    lines = [ln for ln in get_crontab() if CRON_TAG in ln]
    if not lines:
        warn("등록된 TVCAST cron 항목이 없습니다.")
        return
    info("현재 TVCAST cron 항목:")
    for ln in lines:
        say(f"   {ln}", style="dim")


def show_schedules(cfg):
    """config.json 에 정의된 스케줄 목록 표시 (start → end 한 눈에)"""
    schedules = cfg.get("schedules", [])
    videos = cfg.get("videos", {})
    if not schedules:
        warn("config 에 스케줄이 없습니다.")
        return

    if HAS_RICH:
        t = Table(title="config 에 정의된 일정", box=box.ROUNDED, title_style="bold cyan")
        t.add_column("", justify="center")
        t.add_column("이름", style="bold")
        t.add_column("켜기", style="green")
        t.add_column("영상")
        t.add_column("끄기", style="red")
        t.add_column("요일", style="cyan")
        t.add_column("볼륨", justify="right")
        for sc in schedules:
            mark = "✅" if sc.get("enabled") else "⬜"
            vid = str(sc.get("video", "?"))
            vname = videos.get(vid, {}).get("name", "?")
            start = str(sc.get("start", "")).strip() or "-"
            end = str(sc.get("end", "")).strip() or "자동종료없음"
            days = ",".join(sc.get("days", []))
            vol = sc.get("volume")
            vol_txt = str(vol) if vol is not None else "기본"
            t.add_row(mark, Text(sc.get("name", "")), start,
                      Text(f"{vid}.{vname}"), end, Text(days), vol_txt)
        _console.print(t)
        return

    say("config 에 정의된 일정:")
    for sc in schedules:
        mark = "✅" if sc.get("enabled") else "⬜"
        vid = str(sc.get("video", "?"))
        vname = videos.get(vid, {}).get("name", "?")
        start = str(sc.get("start", "")).strip() or "-"
        end = str(sc.get("end", "")).strip()
        days = ",".join(sc.get("days", []))
        vol = sc.get("volume")
        vol_txt = f", 볼륨 {vol}" if vol is not None else ""
        say(f"   {mark} {sc.get('name', '')}")
        tail = f"{end} 끄기" if end else "자동 종료 없음"
        say(f"        {start} 켜기 [{vid}.{vname}]  →  {tail}   (요일: {days}{vol_txt})")


# ====================================================================
#  설정 관리  —  catt 경로를 자동 탐지해 config.json 에 저장 (cron 안전)
# ====================================================================

def find_catt_path(cfg):
    """
    catt 실행파일의 절대경로를 찾는다. 못 찾으면 None.
    cron 은 최소 PATH 로 실행돼 bare 'catt' 를 못 찾으므로 절대경로가 필요하다.
    """
    from shutil import which

    # 1) 현재 설정값이 절대경로이고 실제 존재하면 그대로 사용
    cur = str(get_setting(cfg, "catt_path", "")).strip()
    if cur and os.path.isabs(cur) and os.path.exists(cur):
        return cur

    # 2) PATH 에서 검색 (현재 셸의 PATH 기준)
    found = which(cur) if cur else None
    if not found:
        found = which("catt")
    if found:
        return os.path.abspath(found)

    # 3) 흔한 설치 위치를 직접 확인 (라즈베리파이/리눅스/맥)
    home = os.path.expanduser("~")
    for cand in [os.path.join(home, ".local", "bin", "catt"),
                 "/usr/local/bin/catt", "/usr/bin/catt", "/opt/homebrew/bin/catt"]:
        if os.path.exists(cand):
            return cand
    return None


def set_catt_path(cfg):
    """catt 절대경로를 자동 탐지(실패 시 수동 입력)해 settings.catt_path 에 저장"""
    info("🔍 catt 실행파일 경로를 찾는 중...")
    found = find_catt_path(cfg)

    if not found:
        error("catt 를 자동으로 찾지 못했습니다. ('pip install catt' 했는지 확인하세요)")
        manual = ask_text("경로를 직접 붙여넣기 (취소는 비우고 Enter)")
        if not manual or manual == "q":
            warn("취소했습니다.")
            return
        if not os.path.exists(manual):
            if not ask_confirm("그 경로에 파일이 없습니다. 그래도 저장할까요?", default=False):
                warn("취소했습니다.")
                return
        found = manual

    cfg.setdefault("settings", {})["catt_path"] = found
    if save_config(cfg):
        success(f"✅ catt 경로를 config.json 에 저장했습니다: {found}")
        info("   → cron 자동실행 때도 이 절대경로로 catt 를 찾습니다.")


def set_default_volume(cfg):
    """전체 기본 볼륨(settings.volume)을 설정하거나 해제"""
    cur = get_setting(cfg, "volume", None)
    info(f"현재 기본 볼륨: {cur if cur is not None else '(설정 안 함 — TV 볼륨 그대로 둠)'}")
    raw = ask_text("기본 볼륨 0~100 (비우면 '설정 안 함')")
    if raw in ("", "q"):
        cfg.setdefault("settings", {})["volume"] = None  # 키는 남기고 값만 비움(현황 표시용)
        if save_config(cfg):
            success("🔊 기본 볼륨 해제 — 재생 시 TV 볼륨을 건드리지 않습니다.")
        return
    vol = parse_volume(raw)
    if vol is None:
        error("0~100 사이 숫자를 입력하세요.")
        return
    cfg.setdefault("settings", {})["volume"] = vol
    if save_config(cfg):
        success(f"🔊 기본 볼륨을 {vol} 로 저장했습니다. (재생할 때마다 적용)")


def manage_settings(cfg):
    """설정 서브메뉴 — 기기 선택 · catt 경로 · 기본 볼륨 · 고급(cron 확인)"""
    while True:
        clear_screen()
        dev = get_device(cfg)
        catt = get_setting(cfg, "catt_path", "") or "(미설정)"
        vol = get_setting(cfg, "volume", None)
        vol_txt = vol if vol is not None else "설정 안 함"
        info(f"기기: {dev}    |    기본 볼륨: {vol_txt}    |    catt: {catt}")
        action = select_menu("⚙️  설정", [
            ("🔎 Cast 기기 검색 & 선택", "device"),
            ("🔊 기본 볼륨 설정", "volume"),
            ("📁 catt 경로 자동 찾기 (cron 안전)", "catt"),
            ("📜 실제 적용된 cron 보기 (고급)", "showcron"),
            ("↩  뒤로", "back"),
        ])
        if action == "device":
            choose_device(cfg)
            pause()
        elif action == "volume":
            set_default_volume(cfg)
            pause()
        elif action == "catt":
            set_catt_path(cfg)
            pause()
        elif action == "showcron":
            show_cron()
            pause()
        else:  # back / None
            return


# ====================================================================
#  영상 리스트(즐겨찾기) 관리  —  config.json 의 videos 를 메뉴에서 편집
# ====================================================================

def sorted_videos(cfg):
    """videos 를 (즐겨찾기 먼저, 그 다음 키 순) 으로 정렬해 (key, v) 리스트 반환"""
    def keyfn(item):
        k, v = item
        fav = 0 if v.get("fav") else 1
        try:
            ik = int(k)
        except (ValueError, TypeError):
            ik = 9999
        return (fav, ik, str(k))
    return sorted(cfg.get("videos", {}).items(), key=keyfn)


def next_video_key(videos):
    """videos 에서 안 쓰는 다음 숫자 키 반환 ('1','2'...)"""
    nums = [int(k) for k in videos if str(k).isdigit()]
    return str(max(nums) + 1) if nums else "1"


def pick_video(cfg, title="영상 선택"):
    """영상 리스트에서 하나를 골라 video key 를 반환 (취소 시 None)"""
    if not cfg.get("videos"):
        warn("등록된 영상이 없습니다. 먼저 '영상 추가' 로 등록하세요.")
        return None
    options = []
    for key, v in sorted_videos(cfg):
        star = "⭐ " if v.get("fav") else ""
        options.append((f"{star}{v.get('name', '')}", key))
    options.append(("↩  취소", "__cancel__"))
    chosen = select_menu(title, options)
    return None if chosen == "__cancel__" else chosen


def add_video(cfg):
    """이름 + URL 을 입력받아 videos 에 추가하고 config 저장"""
    name = ask_text("영상 이름")
    if not name or name == "q":
        warn("취소했습니다.")
        return
    url = ask_text("YouTube URL")
    if not url or url == "q":
        warn("취소했습니다.")
        return
    if "http" not in url:
        warn("URL 형식이 아닌 것 같습니다. 그래도 저장합니다.")
    fav = ask_confirm("⭐ 즐겨찾기로 맨 위에 고정할까요?", default=False)

    videos = cfg.setdefault("videos", {})
    key = next_video_key(videos)
    videos[key] = {"name": name, "url": url}
    if fav:
        videos[key]["fav"] = True
    if save_config(cfg):
        star = " ⭐" if fav else ""
        success(f"✅ [{key}] '{name}'{star} 추가됨")


def delete_video(cfg):
    """영상을 골라 삭제 (스케줄에서 쓰는 중이면 경고)"""
    key = pick_video(cfg, "삭제할 영상")
    if key is None:
        return
    name = cfg["videos"][key].get("name", "")
    used = [s.get("name", "?") for s in cfg.get("schedules", [])
            if str(s.get("video")) == key]
    if used:
        warn(f"이 영상은 스케줄에서 사용 중입니다: {', '.join(used)}")
    if not ask_confirm(f"'{name}' 정말 삭제할까요?", default=False):
        warn("취소했습니다.")
        return
    del cfg["videos"][key]
    if save_config(cfg):
        success(f"🗑  '{name}' 삭제됨")


def toggle_favorite(cfg):
    """영상의 ⭐ 즐겨찾기 고정을 켜고 끔"""
    key = pick_video(cfg, "⭐ 즐겨찾기를 켜고 끌 영상")
    if key is None:
        return
    v = cfg["videos"][key]
    v["fav"] = not v.get("fav", False)
    if not v["fav"]:
        v.pop("fav", None)  # false 는 굳이 저장 안 함 (config 깔끔하게)
    if save_config(cfg):
        if cfg["videos"][key].get("fav"):
            success(f"⭐ 즐겨찾기 고정: {v.get('name', '')}")
        else:
            success(f"☆ 고정 해제: {v.get('name', '')}")


def rename_video(cfg):
    """영상 이름 변경"""
    key = pick_video(cfg, "이름을 바꿀 영상")
    if key is None:
        return
    new = ask_text("새 이름")
    if not new or new == "q":
        warn("취소했습니다.")
        return
    cfg["videos"][key]["name"] = new
    if save_config(cfg):
        success(f"✏  이름 변경됨: {new}")


def manage_videos(cfg):
    """영상(즐겨찾기) 관리 서브메뉴"""
    while True:
        clear_screen()
        action = select_menu("🎬 영상 관리 (즐겨찾기)", [
            ("➕ 영상 추가 (이름 + URL)", "a"),
            ("⭐ 즐겨찾기 켜기/끄기", "f"),
            ("✏  이름 변경", "n"),
            ("🗑  영상 삭제", "r"),
            ("↩  뒤로", "back"),
        ])
        if action == "a":
            add_video(cfg)
            pause()
        elif action == "f":
            toggle_favorite(cfg)
            pause()
        elif action == "n":
            rename_video(cfg)
            pause()
        elif action == "r":
            delete_video(cfg)
            pause()
        else:  # back / None(Ctrl-C)
            return


# ====================================================================
#  스케줄 관리  —  리스트에서 영상을 골라 스케줄 추가/삭제
# ====================================================================

def add_schedule(cfg):
    """영상 리스트에서 골라 스케줄을 만들고 config 에 저장"""
    key = pick_video(cfg, "스케줄에 사용할 영상")
    if key is None:
        return
    vname = cfg["videos"][key].get("name", "")

    name = ask_text("스케줄 이름 (비우면 자동)") or f"{vname} 자동재생"
    start = ask_text("켜는 시간 HH:MM")
    if time_to_cron(start) is None:
        error("시간 형식이 잘못되었습니다. (예: 07:15)")
        return
    end = ask_text("끄는 시간 HH:MM (자동종료 없으면 비우기)")
    if end and time_to_cron(end) is None:
        error("끄는 시간 형식이 잘못되었습니다.")
        return
    days_raw = ask_text("요일 (평일/주말/매일 또는 월,수,금)") or "매일"
    days = [d.strip() for d in days_raw.split(",") if d.strip()]
    vol = parse_volume(ask_text("볼륨 0~100 (비우면 기본 볼륨 사용)"))

    sched = {
        "name": name, "enabled": True, "video": key,
        "start": start, "end": end, "days": days,
    }
    if vol is not None:
        sched["volume"] = vol
    cfg.setdefault("schedules", []).append(sched)
    if save_config(cfg):
        vol_txt = f", 볼륨 {vol}" if vol is not None else ""
        success(f"✅ 예약 '{name}' 추가 & 켜짐  (영상: {vname}{vol_txt})")
        report_cron(cfg)  # 켜진 상태로 추가되므로 바로 cron 에 반영


def delete_schedule(cfg):
    """스케줄을 골라 삭제"""
    schedules = cfg.get("schedules", [])
    if not schedules:
        warn("등록된 스케줄이 없습니다.")
        return
    options = []
    for i, s in enumerate(schedules):
        mark = "✅" if s.get("enabled") else "⬜"
        label = f"{mark} {s.get('name', '')}  ({s.get('start', '')}~{s.get('end', '') or '끔없음'})"
        options.append((label, i))
    options.append(("↩  취소", "__cancel__"))
    idx = select_menu("삭제할 스케줄 선택", options)
    if idx is None or idx == "__cancel__":
        warn("취소했습니다.")
        return
    removed = schedules.pop(idx)
    if save_config(cfg):
        success(f"🗑  예약 '{removed.get('name', '')}' 삭제됨")
        report_cron(cfg)  # 삭제 즉시 cron 에서도 제거


def toggle_schedule(cfg):
    """예약을 골라 켜기/끄기 → 즉시 cron 에 반영"""
    schedules = cfg.get("schedules", [])
    if not schedules:
        warn("등록된 예약이 없습니다. 먼저 '새 예약 추가' 를 하세요.")
        return
    options = []
    for i, s in enumerate(schedules):
        mark = "✅ 켜짐" if s.get("enabled") else "⬜ 꺼짐"
        label = f"{mark}  {s.get('name', '')}  ({s.get('start', '')}~{s.get('end', '') or '끔없음'})"
        options.append((label, i))
    options.append(("↩  취소", "__cancel__"))
    idx = select_menu("켜고 끌 예약 선택", options)
    if idx is None or idx == "__cancel__":
        return
    schedules[idx]["enabled"] = not schedules[idx].get("enabled", False)
    state = "켜짐 ✅" if schedules[idx]["enabled"] else "꺼짐 ⬜"
    if save_config(cfg):
        success(f"'{schedules[idx].get('name', '')}' → {state}")
        report_cron(cfg)


def manage_schedules(cfg):
    """자동 예약 서브메뉴 — 켜기/끄기/추가/삭제가 모두 cron 에 즉시 반영됨"""
    while True:
        clear_screen()
        show_schedules(cfg)
        n_on = sum(1 for s in cfg.get("schedules", []) if s.get("enabled"))
        info(f"현재 {n_on}건 켜짐 — 켜고/끄고/추가/삭제하면 cron 에 바로 반영됩니다.")
        action = select_menu("⏰ 자동 예약", [
            ("➕ 새 예약 추가", "add"),
            ("🔘 켜기 / 끄기", "toggle"),
            ("🗑  예약 삭제", "del"),
            ("↩  뒤로", "back"),
        ])
        if action == "add":
            add_schedule(cfg)
            pause()
        elif action == "toggle":
            toggle_schedule(cfg)
            pause()
        elif action == "del":
            delete_schedule(cfg)
            pause()
        else:  # back / None
            return


# ====================================================================
#  메뉴
# ====================================================================

def build_main_menu(cfg):
    """메인 메뉴 옵션 [(label, value), ...] 구성. value None 은 구분선."""
    options = []
    # 즐겨찾기 영상은 맨 위에서 한 번에 재생
    favs = [(k, v) for k, v in sorted_videos(cfg) if v.get("fav")]
    for key, v in favs:
        options.append((f"▶  ⭐ {v.get('name', '')}", f"play:{key}"))
    if favs:
        options.append(("──────────", None))

    options += [
        ("▶  재생할 영상 고르기", "play_pick"),
        ("■  재생 종료", "stop"),
        ("ℹ  현재 상태", "status"),
        ("──────────", None),
        ("⏰ 자동 예약 (스케줄)", "schedules"),
        ("🎬 영상 목록 관리", "videos"),
        ("⚙️  설정 (기기·catt 경로)", "settings"),
        ("──────────", None),
        ("🚪 종료", "quit"),
    ]
    return options


def interactive():
    if not HAS_Q:
        warn("[안내] 'pip install questionary' 하면 화살표(↑↓)로 메뉴를 선택할 수 있어요.")
    elif not HAS_RICH:
        warn("[안내] 'pip install rich' 하면 더 보기 좋은 컬러로 나옵니다.")

    while True:
        cfg = load_config()  # 매번 다시 읽어 config 변경 즉시 반영
        clear_screen()
        device = get_device(cfg)
        n_on = sum(1 for s in cfg.get("schedules", []) if s.get("enabled"))
        title = f"TV Cast  —  기기: {device}   ⏰ 자동예약 {n_on}건 켜짐"
        choice = select_menu(title, build_main_menu(cfg))

        # 즉시 동작(재생/종료/상태)은 결과를 보여준 뒤 Enter 로 메뉴 복귀.
        # 서브메뉴는 자체 루프가 있어 pause 불필요.
        if choice is None or choice == "quit":
            info("종료합니다.")
            return
        elif choice.startswith("play:"):
            play_video(cfg, choice.split(":", 1)[1])
            pause()
        elif choice == "play_pick":
            key = pick_video(cfg, "재생할 영상")
            if key:
                play_video(cfg, key)
                pause()
        elif choice == "stop":
            stop_video(cfg)
            pause()
        elif choice == "status":
            show_status(cfg)
            pause()
        elif choice == "schedules":
            manage_schedules(cfg)
        elif choice == "videos":
            manage_videos(cfg)
        elif choice == "settings":
            manage_settings(cfg)


def main():
    # 명령행 인자 모드 (cron 에서 호출)
    if len(sys.argv) >= 2:
        cfg = load_config()
        action = sys.argv[1]
        if action == "play":
            key = sys.argv[2] if len(sys.argv) >= 3 else "1"
            vol = parse_volume(sys.argv[3]) if len(sys.argv) >= 4 else None
            play_video(cfg, key, vol)
        elif action == "stop":
            stop_video(cfg)
        elif action == "status":
            show_status(cfg)
        elif action == "scan":
            choose_device(cfg)
        elif action == "detect-catt":
            set_catt_path(cfg)
        elif action == "sync-cron":
            report_cron(cfg)
        else:
            error(f"알 수 없는 명령: {action}")
        return

    # 인자 없으면 대화형
    interactive()


if __name__ == "__main__":
    main()
