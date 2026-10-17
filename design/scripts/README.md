# design/scripts

Photocraft를 조종해서 `design/*.pcraft` 원본을 처음부터 다시 그리는 스크립트. 디자인을 고칠 때는 여기 숫자를 고치고 다시 그린 뒤 `tools/export.py`부터 다시 돌린다.

| 파일 | 하는 일 |
|---|---|
| `pc.py` | 실행 중인 앱(`photocraft --control`)에 붙는 클라이언트. 명령 하나나 `-f steps.json`(`[[method, params], ...]`)을 보낸다. |
| `pcb.py` | 같은 클라이언트를 창 없는 `photocraft-cli serve --port`용 기본값으로 쓴다. 앱에 잘못 붙지 않게 포트·토큰을 따로 둔다. |
| `draw_b.py` | Petalpoint(B 파스텔) 17개를 창 없이 그린다. `b-pastel.pcraft`, `b-pastel*.png`, `b-pastel-spec.json`을 쓴다. |
| `draw_d17.py` | Tailpoint(D 캡슐) 17개를 실행 중인 앱에 그린다. `d-capsule.pcraft`, `d-capsule.png`, `d-capsule-spec.json`을 쓴다. `redraw`(열린 d-capsule을 비우고 다시), `finish`(손으로 고친 뒤 저장·내보내기만), `spec`(그리지 않고 spec만 출력). |

## 연결 설정

| | `pc.py`(앱) | `pcb.py`(창 없이) |
|---|---|---|
| 포트 | `--port` → `PHOTOCRAFT_PORT` → 7878 | `--port` → `PHOTOCRAFT_SERVE_PORT` → 7879 |
| 토큰 파일 | `--token-file` → `PHOTOCRAFT_CONTROL_TOKEN_FILE` → `~/.photocraft/control-<포트>.token` | `--token-file` → `PHOTOCRAFT_SERVE_TOKEN_FILE` → `~/.photocraft/control-<포트>.token` |

`--port`와 `--token-file`은 draw 스크립트에도 그대로 넘길 수 있다. 토큰 파일은 저장소 밖에 두고, 내용을 출력하거나 저장소에 넣지 않는다. Photocraft는 토큰 파일이 없으면 새로 만들지만 폴더는 만들지 않으므로 먼저 `~/.photocraft`를 만든다.

## 창 없이 그리기 (Petalpoint)

스크립트는 TCP로 붙으므로 `serve`를 stdio가 아니라 `--port`로 띄운다. 쓰기 루트는 출력 폴더를 담아야 한다.

```
photocraft-cli serve --port 7879 --control-token-file %USERPROFILE%\.photocraft\control-7879.token --automation-read-root C:/Users/spila/Documents/window_cursor --automation-write-root C:/Users/spila/Documents/window_cursor
python design\scripts\draw_b.py
```

- `--out DIR`: 출력 폴더(기본 `design/`). 원본을 덮지 않고 시험할 때는 다른 폴더를 주고, 서버의 `--automation-write-root`도 그 폴더(또는 그 위)로 준다.
- 빈 세션에서 15초쯤 걸린다. 같은 숫자면 같은 그림이 나온다(`doc.render`가 픽셀 단위로 같음).

## 앱에 그리기 (Tailpoint)

```
photocraft --control 7878 --control-token-file %USERPROFILE%\.photocraft\control-7878.token --automation-read-root C:/Users/spila/Documents/window_cursor --automation-write-root C:/Users/spila/Documents/window_cursor
python design\scripts\draw_d17.py
```

- 앱이 경로를 쓰기 루트 기준으로 받으므로 `--out`은 프로젝트 기준 상대 경로로 준다(기본 `design`).
- `redraw`는 열린 문서 이름이 `d-capsule`로 시작하지 않으면 멈춘다.
