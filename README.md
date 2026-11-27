# 楼宇数字标牌编排（signboard）

**所属领域：应用开发 · 子方向：全栈开发**

全栈产品：素材入库 → 屏幕台账 → 播放清单 → 时段窗 → 发布上屏 → 紧急撤屏 → 单屏覆盖 → 变更历史 → 在线求值。

## 骨架一句话

楼宇屏幕按清单与时段窗编排内容，发布须版本落库才能上屏，紧急撤屏与变更历史同源。

## H3 切口候选（写路径互斥 · 须择一）

1. **发布上屏**：同事务写入正式播放配置并升版本锁旧版，或只写影子发布草案、升正时原子落正式区（正式区升正前零改）。
2. **紧急撤屏**：同事务立即写正式空屏并记变更，或影子撤屏草案确认后原子撤屏。
3. **单屏覆盖**：同事务写正式覆盖条目，或影子覆盖草案升正。

正确性口径只能走甲乙中的一种且两边不得混用。

## 一键起环境

```bash
docker compose up -d --build
```

浏览器：http://localhost:11730

- `admin` / `admin123`（管理员）
- `writer` / `writer123`（编排员）
- `viewer` / `viewer123`（只读）

## 本地开发

```powershell
cd projects/18-signboard
$env:PYTHONPATH="backend"
python -m uvicorn app:app --app-dir backend --reload --port 8030
python -m pytest
python scripts/count_loc.py
```

## 空桩

- 远程推屏桥（`remote_push`）：固定返回 501。
- 媒资中台桥接（`media_bridge`）：signboard-13 起**不再是空成功**，已补齐校验和校验后置可播。

## signboard-13「媒资桥校验校验和后才可播」

正确性口径当日拍板并锁死为 **甲路径**（见 `docs/signboard-13-命运说明.md`），不与乙路径混用：

- 媒资桥回传 `remote_checksum` 必须与素材台账校验和一致，才在**同一事务**内写
  `media_verifications` 结果并把素材置 `ready / verified`。
- 不一致或校验和缺失：整笔拒写 ready，同事务把素材压回/保持 `draft / mismatch`，
  正式可播零增；该素材随后任何置可播（`PATCH /api/assets/{id}`、`POST /api/assets/{id}/ready`）
  一律 409，直到重新校验一致。
- 清单“可播齐套”由清单服务**联查** `assets.status + checksum_status + media_verifications`
  判定（`complete / playable_count / fake_ready`），ready 但未核/核败的素材不再算齐套。
- 媒资桥结果、素材状态、`audit_log`、`change_history` 同事务跨表落账（审计/历史写入支持并入调用方事务）。
- 全局互斥名逐字为 **「媒资桥置可播互斥」**（`media_svc.MEDIA_LOCK_KEY`）：进程内非阻塞锁 +
  SQLite `BEGIN IMMEDIATE` + `write_locks` 命名行锁（兼容 `/api/locks` 外部持锁）。
  双会话争用恰一成一败，败方 409 且零正式落库。

三轨红测（先全红复现缺口，再转绿）：

```bash
python -m pytest tests/feat_media_ck_red.py   # 轨道A：校验失败不得 ready
python -m pytest tests/feat_media_pl_fake.py  # 轨道B：清单不得把未校验素材当齐套
python -m pytest tests/feat_media_race.py     # 轨道C：命名互斥争用，败方零正式
```

压锁脚本（调用 `race_media_checksum`）：

```bash
python scripts/race_media_checksum.py 20      # 或 scripts/press_media_lock.py 20
```

主要接口：`POST /api/stubs/media_bridge`（近义 `/verify`、`/sync`，顶层 `/api/media/verify|check|bridge`）、
`GET /api/stubs/media_bridge/results`、`POST /api/stubs/media_bridge/race-selftest`、
`POST /api/assets/{id}/ready|verify|checksum`、`GET /api/playlists/{id}/readiness`。
鉴权 `auth.py` 与库初始化 `db.py` 保持原样；新表结构由 `media_svc` 幂等自建。

