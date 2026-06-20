#!/usr/bin/env python3
"""
TV Cast 웹 리모컨 — 폰/PC 브라우저에서 제어 (기본 포트 8888)

- tvcast.py 의 로직을 그대로 재사용하고 같은 config.json 을 공유한다.
  (CLI·cron·웹이 한 설정을 공유 → 어디서 바꿔도 즉시 반영)

실행:
  pip install flask
  python3 tvcast_web.py            # http://<이 기기 IP>:8888
  TVCAST_PORT=9000 python3 tvcast_web.py   # 포트 변경
"""

import json
import os
import sys

try:
    from flask import Flask, request, jsonify, Response
except ImportError:
    print("[오류] Flask 가 필요합니다.  pip install flask")
    print("       (데비안/우분투에서 막히면: pip install --break-system-packages flask)")
    sys.exit(1)

import tvcast as tv  # 기존 로직 재사용

app = Flask(__name__)


# ---------- config 헬퍼 (web 에서는 sys.exit 대신 예외/빈값) ----------

def load():
    try:
        with open(tv.CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def state_dict(cfg):
    videos = []
    for k, v in tv.sorted_videos(cfg):
        videos.append({"key": k, "name": v.get("name", ""),
                       "url": v.get("url", ""), "fav": bool(v.get("fav"))})
    schedules = []
    for i, s in enumerate(cfg.get("schedules", [])):
        schedules.append({"idx": i, "name": s.get("name", ""),
                          "enabled": bool(s.get("enabled")),
                          "video": str(s.get("video", "")),
                          "start": s.get("start", ""), "end": s.get("end", ""),
                          "days": s.get("days", []), "volume": s.get("volume")})
    return {
        "device_name": cfg.get("device_name", ""),
        "device_ip": cfg.get("device_ip", ""),
        "device": (str(cfg.get("device_name", "")).strip()
                   or str(cfg.get("device_ip", "")).strip() or "(미설정)"),
        "volume": tv.get_setting(cfg, "volume", None),
        "catt_path": tv.get_setting(cfg, "catt_path", ""),
        "videos": videos,
        "schedules": schedules,
        "n_on": sum(1 for s in cfg.get("schedules", []) if s.get("enabled")),
    }


def body():
    return request.get_json(force=True, silent=True) or {}


# ---------- 상태 / 재생 ----------

@app.get("/api/state")
def api_state():
    return jsonify(state_dict(load()))


@app.post("/api/play")
def api_play():
    d = body()
    cfg = load()
    key = str(d.get("video", ""))
    raw = d.get("volume")
    vol = tv.parse_volume(raw) if raw not in (None, "") else None
    tv.play_video(cfg, key, vol)
    return jsonify(ok=True)


@app.post("/api/stop")
def api_stop():
    tv.stop_video(load())
    return jsonify(ok=True)


@app.get("/api/status")
def api_status():
    cfg = load()
    ok, out = tv.run_catt(cfg, ["status"],
                          timeout=tv.get_setting(cfg, "command_timeout", 60))
    return jsonify(ok=ok, text=out or "(상태 정보 없음)")


# ---------- 볼륨 (기본 볼륨 저장 + 지금 바로 적용) ----------

@app.post("/api/volume")
def api_volume():
    d = body()
    cfg = load()
    raw = d.get("value")
    if raw in (None, ""):
        cfg.setdefault("settings", {})["volume"] = None
    else:
        v = tv.parse_volume(raw)
        if v is None:
            return jsonify(ok=False, error="0~100 사이 숫자가 필요합니다."), 400
        cfg.setdefault("settings", {})["volume"] = v
        tv.run_catt(cfg, ["volume", str(v)], quiet=True)  # 재생 중이면 즉시 반영
    tv.save_config(cfg)
    return jsonify(ok=True)


# ---------- 영상 목록 ----------

@app.post("/api/videos")
def api_add_video():
    d = body()
    name = str(d.get("name", "")).strip()
    url = str(d.get("url", "")).strip()
    if not name or not url:
        return jsonify(ok=False, error="이름과 URL 이 모두 필요합니다."), 400
    cfg = load()
    videos = cfg.setdefault("videos", {})
    key = tv.next_video_key(videos)
    videos[key] = {"name": name, "url": url}
    if d.get("fav"):
        videos[key]["fav"] = True
    tv.save_config(cfg)
    return jsonify(ok=True, key=key)


@app.put("/api/videos/<key>")
def api_update_video(key):
    d = body()
    cfg = load()
    v = cfg.get("videos", {}).get(key)
    if not v:
        return jsonify(ok=False, error="없는 영상"), 404
    if "name" in d:
        v["name"] = str(d["name"]).strip() or v.get("name", "")
    if "fav" in d:
        if d["fav"]:
            v["fav"] = True
        else:
            v.pop("fav", None)
    tv.save_config(cfg)
    return jsonify(ok=True)


@app.delete("/api/videos/<key>")
def api_del_video(key):
    cfg = load()
    if key in cfg.get("videos", {}):
        del cfg["videos"][key]
        tv.save_config(cfg)
    return jsonify(ok=True)


# ---------- 자동 예약 (변경 시 cron 자동 동기화) ----------

@app.post("/api/schedules")
def api_add_sched():
    d = body()
    cfg = load()
    vid = str(d.get("video", ""))
    if vid not in cfg.get("videos", {}):
        return jsonify(ok=False, error="영상을 골라주세요."), 400
    start = str(d.get("start", "")).strip()
    if tv.time_to_cron(start) is None:
        return jsonify(ok=False, error="켜는 시간 형식이 잘못됨 (HH:MM)"), 400
    end = str(d.get("end", "")).strip()
    if end and tv.time_to_cron(end) is None:
        return jsonify(ok=False, error="끄는 시간 형식이 잘못됨 (HH:MM)"), 400
    days = d.get("days") or ["매일"]
    if isinstance(days, str):
        days = [x.strip() for x in days.split(",") if x.strip()] or ["매일"]
    raw = d.get("volume")
    vol = tv.parse_volume(raw) if raw not in (None, "") else None
    sched = {"name": str(d.get("name", "")).strip() or "새 예약",
             "enabled": True, "video": vid,
             "start": start, "end": end, "days": days}
    if vol is not None:
        sched["volume"] = vol
    cfg.setdefault("schedules", []).append(sched)
    tv.save_config(cfg)
    tv.sync_cron(cfg, verbose=False)
    return jsonify(ok=True)


@app.post("/api/schedules/<int:idx>/toggle")
def api_toggle_sched(idx):
    cfg = load()
    s = cfg.get("schedules", [])
    if not (0 <= idx < len(s)):
        return jsonify(ok=False, error="없는 예약"), 404
    s[idx]["enabled"] = not s[idx].get("enabled", False)
    tv.save_config(cfg)
    tv.sync_cron(cfg, verbose=False)
    return jsonify(ok=True, enabled=s[idx]["enabled"])


@app.put("/api/schedules/<int:idx>")
def api_edit_sched(idx):
    d = body()
    cfg = load()
    s = cfg.get("schedules", [])
    if not (0 <= idx < len(s)):
        return jsonify(ok=False, error="없는 예약"), 404
    sc = s[idx]
    vid = str(d.get("video", sc.get("video", "")))
    if vid not in cfg.get("videos", {}):
        return jsonify(ok=False, error="영상을 골라주세요."), 400
    start = str(d.get("start", "")).strip()
    if tv.time_to_cron(start) is None:
        return jsonify(ok=False, error="켜는 시간 형식이 잘못됨 (HH:MM)"), 400
    end = str(d.get("end", "")).strip()
    if end and tv.time_to_cron(end) is None:
        return jsonify(ok=False, error="끄는 시간 형식이 잘못됨 (HH:MM)"), 400
    days = d.get("days") or ["매일"]
    if isinstance(days, str):
        days = [x.strip() for x in days.split(",") if x.strip()] or ["매일"]
    raw = d.get("volume")
    vol = tv.parse_volume(raw) if raw not in (None, "") else None
    sc["name"] = str(d.get("name", "")).strip() or sc.get("name", "예약")
    sc["video"], sc["start"], sc["end"], sc["days"] = vid, start, end, days
    if vol is not None:
        sc["volume"] = vol
    else:
        sc.pop("volume", None)  # 비우면 기본 볼륨 사용
    tv.save_config(cfg)
    tv.sync_cron(cfg, verbose=False)
    return jsonify(ok=True)


@app.delete("/api/schedules/<int:idx>")
def api_del_sched(idx):
    cfg = load()
    s = cfg.get("schedules", [])
    if 0 <= idx < len(s):
        s.pop(idx)
        tv.save_config(cfg)
        tv.sync_cron(cfg, verbose=False)
    return jsonify(ok=True)


# ---------- 기기 / 설정 ----------

@app.post("/api/scan")
def api_scan():
    return jsonify(ok=True, devices=tv.scan_devices(load()))


@app.post("/api/device")
def api_device():
    d = body()
    cfg = load()
    cfg["device_name"] = str(d.get("name", "")).strip()
    cfg["device_ip"] = str(d.get("ip", "")).strip()
    tv.save_config(cfg)
    return jsonify(ok=True)


@app.post("/api/detect-catt")
def api_detect_catt():
    cfg = load()
    found = tv.find_catt_path(cfg)
    if not found:
        return jsonify(ok=False, error="catt 를 찾지 못했습니다."), 404
    cfg.setdefault("settings", {})["catt_path"] = found
    tv.save_config(cfg)
    return jsonify(ok=True, path=found)


# ---------- 페이지 ----------

@app.get("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>TV Cast</title>
<style>
  :root{--bg:#0f1115;--card:#181b22;--line:#2a2e38;--txt:#e8eaed;--mut:#9aa0aa;
        --accent:#3b82f6;--danger:#ef4444;--r:12px;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans KR",sans-serif;
       -webkit-tap-highlight-color:transparent;}
  .wrap{max-width:480px;margin:0 auto;padding:14px 14px 40px;}
  .row{display:flex;align-items:center;gap:8px}
  .between{justify-content:space-between}
  h1{font-size:18px;margin:0;font-weight:600}
  .sub{font-size:12px;color:var(--mut)}
  .card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);
        padding:14px;margin:12px 0;}
  .lbl{font-size:12px;color:var(--mut);margin:0 0 10px}
  button{font:inherit;color:var(--txt);background:#202531;border:1px solid var(--line);
         border-radius:10px;padding:12px 14px;cursor:pointer;display:flex;align-items:center;
         gap:9px;width:100%;justify-content:flex-start}
  button:active{transform:scale(.985)}
  button.center{justify-content:center}
  button.accent{background:var(--accent);border-color:var(--accent);color:#fff}
  button.danger{color:#fff;background:var(--danger);border-color:var(--danger)}
  button.ghost{background:transparent}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .pill{font-size:12px;padding:4px 9px;border-radius:8px}
  .pill.on{background:rgba(34,197,94,.15);color:#4ade80}
  .pill.off{background:#202531;color:var(--mut)}
  input,select{font:inherit;color:var(--txt);background:#11141a;border:1px solid var(--line);
        border-radius:9px;padding:10px;width:100%}
  input[type=range]{padding:0;accent-color:var(--accent)}
  .ico{font-size:18px;min-width:20px;text-align:center}
  .vrow{display:flex;align-items:center;justify-content:space-between;
        padding:11px 0;border-top:1px solid var(--line);gap:10px}
  .vrow:first-child{border-top:none}
  .sw{width:46px;height:26px;border-radius:14px;border:1px solid var(--line);
      background:#202531;position:relative;flex:0 0 auto;cursor:pointer}
  .sw.on{background:var(--accent);border-color:var(--accent)}
  .sw i{position:absolute;top:2px;left:2px;width:20px;height:20px;border-radius:50%;
        background:#fff;transition:left .15s}
  .sw.on i{left:23px}
  .mut{color:var(--mut);font-size:12px;word-break:break-all}
  .x{color:var(--mut);background:transparent;border:none;width:auto;padding:6px}
  .hide{display:none}
  #toast{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);
         background:#000;color:#fff;padding:10px 16px;border-radius:20px;font-size:13px;
         opacity:0;transition:opacity .2s;pointer-events:none;z-index:9}
  #toast.show{opacity:.92}
  details{margin:12px 0}
  summary{cursor:pointer;color:var(--mut);font-size:13px;padding:6px 0;list-style:none}
  summary::-webkit-details-marker{display:none}
  pre{white-space:pre-wrap;word-break:break-all;font-size:12px;color:var(--mut);margin:8px 0 0}
  .field{margin:8px 0}
</style>
</head>
<body>
<div class="wrap">
  <div class="row between" style="margin:6px 2px 0">
    <div>
      <h1><span class="ico">📺</span> TV Cast</h1>
      <div class="sub" id="devsub">기기: …</div>
    </div>
    <span class="pill off" id="schpill">예약 0</span>
  </div>

  <div class="card">
    <p class="lbl">재생</p>
    <div id="favs"></div>
    <div class="grid2" style="margin-top:8px">
      <button class="center danger" id="btn-stop"><span class="ico">■</span>정지</button>
      <button class="center ghost" id="btn-status"><span class="ico">ℹ</span>상태</button>
    </div>
    <pre id="statusbox" class="hide"></pre>
  </div>

  <div class="card">
    <div class="row between"><p class="lbl" style="margin:0">기본 볼륨</p><span id="volval" class="mut">설정 안 함</span></div>
    <input type="range" min="0" max="100" step="1" value="0" id="vol" style="margin-top:12px">
    <button class="ghost center" id="btn-volclear" style="margin-top:10px;font-size:13px">볼륨 해제 (TV 볼륨 그대로)</button>
  </div>

  <div class="card">
    <div class="row between"><p class="lbl" style="margin:0">자동 예약</p><span class="mut" id="schn"></span></div>
    <div id="scheds"></div>
    <details id="sched-details">
      <summary>＋ 예약 추가 / 수정</summary>
      <div class="field"><input id="s_name" placeholder="이름 (예: 아침 뉴스)"></div>
      <div class="field"><select id="s_video"></select></div>
      <div class="grid2">
        <input id="s_start" placeholder="켜기 07:15">
        <input id="s_end" placeholder="끄기 07:55 (선택)">
      </div>
      <div class="grid2 field">
        <input id="s_days" placeholder="평일/주말/매일">
        <input id="s_vol" placeholder="볼륨 (선택)">
      </div>
      <button class="accent center" id="btn-addsched"><span class="ico">＋</span>예약 추가</button>
    </details>
  </div>

  <details>
    <summary>🎬 영상 목록 관리</summary>
    <div class="card" id="vidmgr"></div>
    <div class="card">
      <div class="field"><input id="v_name" placeholder="영상 이름"></div>
      <div class="field"><input id="v_url" placeholder="YouTube URL"></div>
      <label class="row" style="margin:4px 2px 10px;font-size:14px;color:var(--mut)">
        <input type="checkbox" id="v_fav" style="width:auto;margin-right:6px"> ⭐ 즐겨찾기로 추가</label>
      <button class="accent center" id="btn-addvideo"><span class="ico">＋</span>영상 추가</button>
    </div>
  </details>

  <details>
    <summary>⚙️ 기기 · 설정</summary>
    <div class="card">
      <button class="center ghost" id="btn-scan"><span class="ico">🔎</span>Cast 기기 검색</button>
      <div id="devlist"></div>
      <button class="center ghost" id="btn-catt" style="margin-top:8px"><span class="ico">📁</span>catt 경로 자동 찾기</button>
      <div class="mut" id="cattp" style="margin-top:8px"></div>
    </div>
  </details>
</div>

<div id="toast"></div>

<script>
const $=s=>document.querySelector(s);
let S={};
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');clearTimeout(t._);t._=setTimeout(()=>t.classList.remove('show'),1600);}
function h(tag,opts,kids){const e=document.createElement(tag);opts=opts||{};
  if(opts.cls)e.className=opts.cls;
  if(opts.text!=null)e.textContent=opts.text;
  if(opts.on)for(const k in opts.on)e.addEventListener(k,opts.on[k]);
  if(opts.attr)for(const k in opts.attr)e.setAttribute(k,opts.attr[k]);
  (kids||[]).forEach(c=>{if(c==null)return;e.appendChild(typeof c==='string'?document.createTextNode(c):c);});
  return e;}
function ico(t){return h('span',{cls:'ico',text:t});}
async function api(u,m,b){const o={method:m||'GET',headers:{'Content-Type':'application/json'}};if(b)o.body=JSON.stringify(b);
  const r=await fetch(u,o);let j={};try{j=await r.json();}catch(e){}
  if(!r.ok||j.ok===false){toast('⚠ '+(j.error||r.status));throw new Error(j.error||r.status);}return j;}

async function load(){S=await (await fetch('/api/state')).json();render();}
function render(){
  $('#devsub').textContent='기기: '+S.device;
  const p=$('#schpill');p.textContent='예약 '+S.n_on+(S.n_on?' 켜짐':'');p.className='pill '+(S.n_on?'on':'off');
  $('#vol').value=S.volume==null?0:S.volume;
  $('#volval').textContent=S.volume==null?'설정 안 함':S.volume;
  $('#cattp').textContent='catt: '+(S.catt_path||'(미설정)');

  const favs=S.videos.filter(v=>v.fav), list=(favs.length?favs:S.videos).slice(0,6);
  $('#favs').replaceChildren(...(list.length
    ? list.map(v=>h('button',{attr:{style:'margin-bottom:8px'},on:{click:()=>play(v.key)}},[ico(v.fav?'⭐':'▶'),v.name]))
    : [h('div',{cls:'mut',text:'영상이 없습니다. 아래에서 추가하세요.'})]));

  $('#s_video').replaceChildren(...S.videos.map(v=>h('option',{text:v.name,attr:{value:v.key}})));

  $('#schn').textContent=S.schedules.length+'개';
  $('#scheds').replaceChildren(...(S.schedules.length
    ? S.schedules.map(s=>{
        const vol=s.volume==null?'기본 볼륨':'볼륨 '+s.volume;
        const meta=s.start+(s.end?(' → '+s.end):'')+' · '+((s.days||[]).join(','))+' · '+vol;
        const sw=h('div',{cls:'sw '+(s.enabled?'on':''),on:{click:()=>toggleSched(s.idx)}},[h('i')]);
        return h('div',{cls:'vrow'},[
          h('div',{},[h('div',{text:s.name}),h('div',{cls:'mut',text:meta})]),
          h('div',{cls:'row'},[sw,
            h('button',{cls:'x',text:'✏',on:{click:()=>editSched(s)}}),
            h('button',{cls:'x',text:'🗑',on:{click:()=>delSched(s.idx)}})])]);
      })
    : [h('div',{cls:'mut',text:'예약이 없습니다.'})]));

  $('#vidmgr').replaceChildren(...(S.videos.length
    ? S.videos.map(v=>h('div',{cls:'vrow'},[
        h('div',{},[h('div',{text:(v.fav?'⭐ ':'')+v.name}),h('div',{cls:'mut',text:v.url})]),
        h('div',{cls:'row'},[
          h('button',{cls:'x',text:v.fav?'☆':'⭐',on:{click:()=>toggleFav(v.key,v.fav)}}),
          h('button',{cls:'x',text:'✏',on:{click:()=>renameVideo(v.key,v.name)}}),
          h('button',{cls:'x',text:'🗑',on:{click:()=>delVideo(v.key)}})])]))
    : [h('div',{cls:'mut',text:'없음'})]));
}

async function play(k){toast('▶ 재생 중…');await api('/api/play','POST',{video:k});toast('▶ 재생 시작');}
async function setVol(v){await api('/api/volume','POST',{value:v});load();toast(v===''?'볼륨 해제':'🔊 볼륨 '+v);}
async function showStatus(){const j=await api('/api/status');const b=$('#statusbox');b.textContent=j.text;b.classList.remove('hide');}
async function addVideo(){await api('/api/videos','POST',{name:$('#v_name').value,url:$('#v_url').value,fav:$('#v_fav').checked});
  $('#v_name').value='';$('#v_url').value='';$('#v_fav').checked=false;load();toast('✅ 영상 추가');}
async function delVideo(k){if(!confirm('이 영상을 삭제할까요?'))return;await api('/api/videos/'+k,'DELETE');load();toast('🗑 삭제');}
async function toggleFav(k,f){await api('/api/videos/'+k,'PUT',{fav:!f});load();}
async function renameVideo(k,cur){const n=prompt('새 이름',cur);if(n==null)return;await api('/api/videos/'+k,'PUT',{name:n});load();toast('✏ 이름 변경');}

let editIdx=null;
function schedBtnLabel(){$('#btn-addsched').lastChild.textContent=editIdx==null?'예약 추가':'수정 저장';}
function editSched(s){editIdx=s.idx;
  $('#s_name').value=s.name||'';$('#s_video').value=s.video;$('#s_start').value=s.start||'';
  $('#s_end').value=s.end||'';$('#s_days').value=(s.days||[]).join(',');$('#s_vol').value=s.volume==null?'':s.volume;
  $('#sched-details').open=true;schedBtnLabel();$('#s_name').scrollIntoView({block:'center'});toast('✏ 예약 수정 중…');}
async function submitSched(){
  const p={name:$('#s_name').value,video:$('#s_video').value,start:$('#s_start').value,
    end:$('#s_end').value,days:$('#s_days').value,volume:$('#s_vol').value};
  if(editIdx==null){await api('/api/schedules','POST',p);toast('✅ 예약 추가 (cron 반영)');}
  else{await api('/api/schedules/'+editIdx,'PUT',p);toast('✅ 예약 수정 (cron 반영)');}
  editIdx=null;schedBtnLabel();['s_name','s_start','s_end','s_days','s_vol'].forEach(i=>$('#'+i).value='');load();}
async function toggleSched(i){await api('/api/schedules/'+i+'/toggle','POST');load();}
async function delSched(i){if(!confirm('이 예약을 삭제할까요?'))return;await api('/api/schedules/'+i,'DELETE');load();toast('🗑 삭제');}
async function scan(){toast('🔎 검색 중…');const j=await api('/api/scan','POST');const ds=j.devices||[];
  $('#devlist').replaceChildren(...(ds.length
    ? ds.map(d=>h('button',{cls:'ghost',attr:{style:'margin-top:8px'},on:{click:()=>pickDev(d.name,d.ip)},text:d.name+' ['+d.ip+']'}))
    : [h('div',{cls:'mut',attr:{style:'margin-top:8px'},text:'기기를 못 찾았습니다.'})]));}
async function pickDev(name,ip){await api('/api/device','POST',{name,ip});load();toast('✅ 기기: '+name);}
async function detectCatt(){const j=await api('/api/detect-catt','POST');load();toast('✅ '+j.path);}

$('#btn-stop').addEventListener('click',()=>api('/api/stop','POST').then(()=>toast('■ 정지')));
$('#btn-status').addEventListener('click',showStatus);
$('#btn-volclear').addEventListener('click',()=>setVol(''));
$('#vol').addEventListener('input',function(){$('#volval').textContent=this.value;});
$('#vol').addEventListener('change',function(){setVol(this.value);});
$('#btn-addsched').addEventListener('click',submitSched);
$('#btn-addvideo').addEventListener('click',addVideo);
$('#btn-scan').addEventListener('click',scan);
$('#btn-catt').addEventListener('click',detectCatt);

load();
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("TVCAST_PORT", "8888"))
    print(f"📺 TV Cast 웹 리모컨 → http://0.0.0.0:{port}")
    print("   같은 WiFi 의 폰/PC 브라우저에서 'http://<이 기기 IP>:%d' 로 접속하세요." % port)
    app.run(host="0.0.0.0", port=port, threaded=True)
