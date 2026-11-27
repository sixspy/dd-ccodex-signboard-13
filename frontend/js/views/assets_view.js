window.SB_VIEWS = window.SB_VIEWS || {};
window.SB_VIEWS["assets-view"] = {
  template: `
  <div>
    <div class="panel">
      <h2>素材台账 · 校验和后才可播</h2>
      <p class="lead">图片 / 视频 / 网页页 / 滚动条。媒资桥回传校验和与台账一致后，素材才判为<span class="badge ok">可播</span>；校验失败整笔拒写 ready，正式保持草稿。</p>
      <div class="row-form">
        <input v-model="form.code" placeholder="编码（小写短码）" />
        <input v-model="form.title" placeholder="标题" />
        <select v-model="form.kind"><option>image</option><option>video</option><option>html</option><option>ticker</option></select>
        <input v-model="form.resolution" placeholder="分辨率" />
        <input v-model="form.checksum" placeholder="台账校验和 checksum" />
        <select v-model="form.status"><option value="draft">draft（推荐）</option><option value="ready">ready（未校验则账实不可播）</option></select>
        <button class="primary" @click="create">登记素材</button>
      </div>
      <p class="err" v-if="error">{{ error }}</p>
      <p class="hint" v-if="notice" style="color:var(--accent2)">{{ notice }}</p>
    </div>

    <div class="panel">
      <div class="filters">
        <button :class="{ on: filter === 'all' }" @click="filter = 'all'">全部 {{ items.length }}</button>
        <button :class="{ on: filter === 'playable' }" @click="filter = 'playable'">账实可播 {{ count.playable }}</button>
        <button :class="{ on: filter === 'unverified' }" @click="filter = 'unverified'">未校验 {{ count.unverified }}</button>
        <button :class="{ on: filter === 'mismatch' }" @click="filter = 'mismatch'">校验不一致 {{ count.mismatch }}</button>
      </div>
      <table>
        <thead><tr>
          <th>编码</th><th>标题</th><th>种类</th><th>分辨率</th>
          <th>校验和</th><th>台账状态</th><th>校验状态</th><th>可播(账实)</th><th>媒资桥校验 / 操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="a in shown" :key="a.id">
            <td>{{ a.code }}<div class="ck">#{{ a.id }}</div></td>
            <td>{{ a.title }}<div class="ck">{{ a.duration_sec }}s · {{ a.kind_label }}</div></td>
            <td>{{ a.kind }}</td>
            <td>{{ a.resolution }}</td>
            <td><span class="ck">{{ a.checksum || '（空）' }}</span></td>
            <td><span :class="['badge', a.status === 'ready' ? 'ok' : (a.status === 'retired' ? 'muted' : 'warn')]">{{ a.status_label }}</span></td>
            <td><span :class="['badge', csClass(a.checksum_status)]">{{ a.checksum_status_label || a.checksum_status }}</span></td>
            <td>
              <span v-if="a.playable" class="badge ok">可播</span>
              <span v-else class="badge bad">不可播</span>
            </td>
            <td>
              <div class="row-actions">
                <input class="mini-input" v-model="remote[a.id]" :placeholder="'桥回传校验和'" />
                <button class="primary" @click="verify(a, false)">校验并置可播</button>
                <button class="ghost" @click="useLocal(a)">取台账值(一致)</button>
                <button class="ghost" @click="setReady(a)">置可播闸口</button>
              </div>
              <p class="err" v-if="rowError[a.id]" style="margin:6px 0 0;max-width:280px">{{ rowError[a.id] }}</p>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>`,
  data() {
    return {
      items: [],
      filter: "all",
      remote: {},
      rowError: {},
      error: "",
      notice: "",
      form: { code: "", title: "", kind: "image", resolution: "1920x1080", status: "draft", duration_sec: 12, checksum: "" }
    };
  },
  computed: {
    shown() {
      if (this.filter === "playable") return this.items.filter(a => a.playable);
      if (this.filter === "unverified") return this.items.filter(a => a.checksum_status !== "verified" && a.checksum_status !== "mismatch");
      if (this.filter === "mismatch") return this.items.filter(a => a.checksum_status === "mismatch");
      return this.items;
    },
    count() {
      return {
        playable: this.items.filter(a => a.playable).length,
        unverified: this.items.filter(a => (a.checksum_status || "unverified") === "unverified").length,
        mismatch: this.items.filter(a => a.checksum_status === "mismatch").length
      };
    }
  },
  async mounted() { await this.reload(); },
  methods: {
    csClass(cs) {
      if (cs === "verified") return "ok";
      if (cs === "mismatch") return "bad";
      return "muted";
    },
    async reload() {
      this.items = (await SB.api("/api/assets")).items;
      for (const a of this.items) {
        if (this.remote[a.id] === undefined) this.remote[a.id] = "";
      }
    },
    useLocal(a) { this.remote[a.id] = a.checksum || ""; this.rowError[a.id] = ""; },
    async create() {
      this.error = ""; this.notice = "";
      try {
        await SB.api("/api/assets", { method: "POST", body: JSON.stringify(this.form) });
        this.notice = "已登记；请用媒资桥校验校验和，一致后才账实可播。";
        this.form.code = ""; this.form.title = ""; this.form.checksum = "";
        await this.reload();
      } catch (e) { this.error = e.message; }
    },
    async verify(a) {
      this.rowError[a.id] = "";
      try {
        const res = await SB.api("/api/stubs/media_bridge", {
          method: "POST",
          body: JSON.stringify({ asset_id: a.id, remote_checksum: this.remote[a.id] })
        });
        this.notice = `#${a.id} ${res.matched ? "校验一致，已置可播" : "校验未通过"}`;
        await this.reload();
      } catch (e) {
        this.rowError[a.id] = (e.data && e.data.detail) || e.message;
        await this.reload();
      }
    },
    async setReady(a) {
      this.rowError[a.id] = "";
      try {
        await SB.api(`/api/assets/${a.id}/ready`, { method: "POST", body: "{}" });
        this.notice = `#${a.id} 已通过置可播闸口`;
        await this.reload();
      } catch (e) {
        this.rowError[a.id] = e.message;
      }
    }
  }
};
