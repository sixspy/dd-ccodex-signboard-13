window.SB_VIEWS = window.SB_VIEWS || {};
window.SB_VIEWS["history-view"] = {
  template: `
  <div class="panel">
    <h2>变更历史 · 媒资校验轨迹同源可追溯</h2>
    <p class="lead">发布、撤屏、覆盖、台账与媒资桥校验/拒写都落 change_history；媒资置可播结果与审计、素材状态同事务。</p>

    <div class="filters">
      <button :class="{ on: mode === 'history' }" @click="mode = 'history'">变更历史</button>
      <button :class="{ on: mode === 'audit' }" @click="switchAudit">审计日志 audit_log</button>
    </div>

    <div class="filters" v-if="mode === 'history'">
      <button :class="{ on: kind === '' }" @click="setKind('')">全部</button>
      <button :class="{ on: kind === 'media_bridge' }" @click="setKind('media_bridge')">媒资校验</button>
      <button :class="{ on: kind === 'asset' }" @click="setKind('asset')">素材</button>
      <button :class="{ on: kind === 'playlist' }" @click="setKind('playlist')">清单</button>
      <button :class="{ on: kind === 'publication' }" @click="setKind('publication')">发布</button>
    </div>

    <table v-if="mode === 'history'">
      <thead><tr><th>ID</th><th>实体</th><th>动作</th><th>详情</th><th>操作者</th><th>时间</th></tr></thead>
      <tbody>
        <tr v-for="h in items" :key="h.id">
          <td>{{ h.id }}</td>
          <td>
            <span :class="['badge', h.entity_kind === 'media_bridge' ? 'info' : 'muted']">{{ h.entity_kind }}#{{ h.entity_id }}</span>
          </td>
          <td>{{ h.action }}</td>
          <td>{{ h.detail }}</td>
          <td>{{ h.actor }}</td>
          <td class="ck">{{ fmt(h.created_at) }}</td>
        </tr>
      </tbody>
    </table>

    <table v-else>
      <thead><tr><th>ID</th><th>操作者</th><th>动作</th><th>载荷</th><th>时间</th></tr></thead>
      <tbody>
        <tr v-for="a in audits" :key="a.id">
          <td>{{ a.id }}</td><td>{{ a.actor }}</td>
          <td><span :class="['badge', (a.action||'').includes('mismatch') ? 'bad' : 'ok']">{{ a.action }}</span></td>
          <td><span class="ck">{{ a.payload }}</span></td>
          <td class="ck">{{ fmt(a.created_at) }}</td>
        </tr>
      </tbody>
    </table>
  </div>`,
  data() { return { mode: "history", kind: "", items: [], audits: [] }; },
  async mounted() { await this.reload(); },
  methods: {
    fmt(ts) { try { return new Date((ts || 0) * 1000).toLocaleString("zh-CN", { hour12: false }); } catch (e) { return ts; } },
    async reload() {
      const q = this.kind ? `?limit=200&entity_kind=${encodeURIComponent(this.kind)}` : "?limit=200";
      this.items = (await SB.api("/api/history" + q)).items;
    },
    async setKind(k) { this.kind = k; await this.reload(); },
    async switchAudit() {
      this.mode = "audit";
      if (!this.audits.length) this.audits = (await SB.api("/api/audit/recent?limit=200")).items;
    }
  }
};
