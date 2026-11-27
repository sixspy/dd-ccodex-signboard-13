window.SB_VIEWS = window.SB_VIEWS || {};
window.SB_VIEWS["stubs-view"] = {
  template: `
  <div>
    <div class="panel">
      <h2>空桩桥 · 媒资中台桥接（signboard-13）</h2>
      <p class="lead">远程推屏桥仍为空桩（501）；媒资桥已补齐：必须校验校验和一致才允许素材置可播，失败整笔拒写 ready。</p>
      <p class="lock-note">全局互斥名：<code class="ck">媒资桥置可播互斥</code> —— 双会话争用时败方 409 且零正式落库。</p>
    </div>

    <div class="panel">
      <h3>媒资桥同步 + 校验置可播</h3>
      <div class="row-form">
        <select v-model="assetId" @change="onPick" style="min-width:220px">
          <option :value="null" disabled>选择素材…</option>
          <option v-for="a in assets" :key="a.id" :value="a.id">
            #{{ a.id }} {{ a.code }}（台账 {{ a.checksum || '空' }} / {{ a.status }}）
          </option>
        </select>
        <input class="mini-input" style="min-width:240px" v-model="remoteCk" placeholder="媒资中台回传 remote_checksum" />
        <button class="ghost" @click="fillMatch">填成一致</button>
        <button class="ghost" @click="fillMismatch">填成不一致</button>
        <button class="primary" @click="sync">同步并校验置可播</button>
        <button class="danger" @click="raceSelfTest" :disabled="racing">{{ racing ? '压锁中…' : '争用自检 race_media_checksum' }}</button>
      </div>

      <div v-if="verdict" :class="['verdict', verdict.cls]">{{ verdict.text }}</div>

      <div v-if="race" class="verdict" :class="race.ok ? 'good' : 'bad'">
        压锁 {{ race.rounds }} 轮：{{ race.ok ? 'PASS ✅ 每轮恰一成一败，败方零正式' : 'FAIL ❌' }}
        <span v-if="!race.ok"> 异常轮：{{ failedRounds }}</span>
      </div>

      <h3 style="margin-top:18px">媒资桥结果 × 素材状态（同事务跨表联查）</h3>
      <table>
        <thead><tr>
          <th>#</th><th>素材</th><th>本地/期望校验和</th><th>桥回传校验和</th>
          <th>比对</th><th>当笔状态</th><th>当前素材</th><th>操作者</th><th>来源</th>
        </tr></thead>
        <tbody>
          <tr v-for="v in results" :key="v.id">
            <td>{{ v.id }}</td>
            <td>#{{ v.asset_id }} {{ v.asset_code }}</td>
            <td><span class="ck">{{ v.local_checksum || '（空）' }}</span></td>
            <td><span class="ck">{{ v.remote_checksum || '（空）' }}</span></td>
            <td><span :class="['badge', v.matched ? 'ok' : 'bad']">{{ v.matched ? '一致' : (v.reason === 'checksum_missing' ? '缺失' : '不一致') }}</span></td>
            <td><span :class="['badge', v.status_snapshot === 'ready' ? 'ok' : 'warn']">{{ v.status_snapshot || '—' }}</span></td>
            <td>
              <span :class="['badge', v.asset_status === 'ready' ? 'ok' : 'warn']">{{ v.asset_status }}</span>
              <div class="ck">{{ v.asset_checksum_status }}</div>
            </td>
            <td>{{ v.holder }}</td>
            <td>{{ v.source }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>远程推屏桥（保持空桩 501）</h3>
      <div class="row-form">
        <button class="ghost" @click="callRemotePush">试调 remote_push</button>
      </div>
      <pre style="white-space:pre-wrap;background:#0d1828;padding:12px;border-radius:10px;">{{ pushOut }}</pre>
    </div>

    <div class="panel">
      <h3>空桩清单</h3>
      <table>
        <thead><tr><th>键</th><th>标题</th><th>说明</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="s in stubs" :key="s.key">
            <td>{{ s.key }}</td><td>{{ s.title }}</td><td>{{ s.desc }}</td>
            <td><span :class="['badge', s.status === 'checksum_gated' ? 'ok' : 'muted']">{{ s.status }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>`,
  data() {
    return {
      assets: [],
      results: [],
      stubs: [],
      assetId: null,
      remoteCk: "",
      verdict: null,
      race: null,
      racing: false,
      pushOut: ""
    };
  },
  computed: {
    selected() { return this.assets.find(a => a.id === this.assetId); },
    failedRounds() {
      if (!this.race || !this.race.details) return "";
      return this.race.details.filter(d => !(d.winners === 1 && d.busy_losers === 1)).map(d => d.round).join(",");
    }
  },
  async mounted() {
    this.stubs = (await SB.api("/api/stubs")).items;
    await this.reload();
  },
  methods: {
    async reload() {
      this.assets = (await SB.api("/api/assets")).items;
      this.results = (await SB.api("/api/stubs/media_bridge/results?limit=100")).items;
    },
    onPick() { if (this.selected) this.remoteCk = this.selected.checksum || ""; },
    fillMatch() { if (this.selected) this.remoteCk = this.selected.checksum || ""; },
    fillMismatch() { this.remoteCk = "remote-DIFFERENT-" + Math.random().toString(16).slice(2, 8); },
    showData(prefix, d, cls) {
      this.verdict = { cls, text: prefix + "\n" + JSON.stringify(d, null, 2) };
    },
    async sync() {
      this.verdict = null;
      if (!this.assetId) { this.verdict = { cls: "bad", text: "请先选择素材" }; return; }
      try {
        const d = await SB.api("/api/stubs/media_bridge", {
          method: "POST",
          body: JSON.stringify({ asset_id: this.assetId, remote_checksum: this.remoteCk })
        });
        this.showData("✅ 校验一致：素材已置可播（同事务跨表落账）", d, "good");
      } catch (e) {
        const d = e.data || { detail: e.message };
        if (d.busy || d.locked) this.showData("⛔ 互斥争用：败方被拒（零正式落库）", d, "lock");
        else this.showData("❌ 校验失败：整笔拒写 ready，素材保持草稿", d, "bad");
      } finally {
        await this.reload();
      }
    },
    async raceSelfTest() {
      this.racing = true; this.race = null;
      try {
        this.race = await SB.api("/api/stubs/media_bridge/race-selftest", {
          method: "POST", body: JSON.stringify({ rounds: 5 })
        });
      } catch (e) {
        this.verdict = { cls: "bad", text: "压锁自检失败：" + e.message };
      } finally {
        this.racing = false;
      }
    },
    async callRemotePush() {
      try {
        await SB.api("/api/stubs/remote_push", { method: "POST", body: JSON.stringify({ screen_id: 1 }) });
      } catch (e) {
        try {
          const res = await fetch("/api/stubs/remote_push", {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: "Bearer " + SB.token },
            body: JSON.stringify({ screen_id: 1 })
          });
          this.pushOut = await res.text();
        } catch (e2) { this.pushOut = String(e2); }
      }
    }
  }
};
