window.SB_VIEWS = window.SB_VIEWS || {};
window.SB_VIEWS["playlists-view"] = {
  template: `
  <div class="panel">
    <h2>播放清单 · 可播齐套联查</h2>
    <p class="lead">有序素材序列；只有条目的校验和均核验一致（账实可播）才算「可播齐套」。台账 ready 但未校验的素材会让清单标红为“假可播”，不再被当成齐套。</p>
    <table>
      <thead><tr>
        <th>编码</th><th>标题</th><th>台账状态</th><th>齐套判定</th>
        <th>条目</th><th>账实可播</th><th>未校验/核败</th><th>过渡</th><th>负责人</th><th></th>
      </tr></thead>
      <tbody>
        <tr v-for="p in items" :key="p.id">
          <td>{{ p.code }}<div class="ck">#{{ p.id }}</div></td>
          <td>{{ p.title }}</td>
          <td><span :class="['badge', p.status === 'ready' ? 'ok' : 'warn']">{{ p.status }}</span></td>
          <td>
            <span v-if="p.complete" class="badge ok">可播齐套</span>
            <span v-else-if="p.item_count === 0" class="badge muted">空清单</span>
            <span v-else class="badge bad">未齐套</span>
            <span v-if="p.fake_ready" class="badge warn" style="margin-left:6px">台账假可播</span>
          </td>
          <td>{{ p.item_count }}</td>
          <td style="color:var(--accent2)">{{ p.playable_count }}</td>
          <td :style="{color: p.unplayable_count ? 'var(--danger)' : 'inherit'}">{{ p.unplayable_count }}</td>
          <td>{{ p.transition }}</td>
          <td>{{ p.owner }}</td>
          <td><button class="ghost" @click="toggle(p.id)">{{ open === p.id ? '收起' : '条目联查' }}</button></td>
        </tr>
        <tr v-if="openDetail">
          <td colspan="10">
            <div class="subtable-wrap">
              <table>
                <thead><tr><th>seq</th><th>素材</th><th>种类</th><th>台账状态</th><th>校验状态</th><th>账实可播</th><th>校验和</th></tr></thead>
                <tbody>
                  <tr v-for="it in openDetail.items" :key="it.id">
                    <td>{{ it.seq }}</td>
                    <td>#{{ it.asset_id }} {{ it.asset_code }} · {{ it.asset_title }}</td>
                    <td>{{ it.kind }}</td>
                    <td>{{ it.status || it.asset_status }}</td>
                    <td><span :class="['badge', csClass(it.checksum_status)]">{{ it.checksum_status_label || it.checksum_status }}</span></td>
                    <td><span :class="['badge', it.playable ? 'ok' : 'bad']">{{ it.playable ? '可播' : '不可播' }}</span></td>
                    <td><span class="ck">{{ it.checksum || '（空）' }}</span></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>`,
  data() { return { items: [], open: null, openDetail: null }; },
  async mounted() { await this.reload(); },
  methods: {
    async reload() { this.items = (await SB.api("/api/playlists")).items; },
    csClass(cs) {
      if (cs === "verified") return "ok";
      if (cs === "mismatch") return "bad";
      return "muted";
    },
    async toggle(id) {
      if (this.open === id) { this.open = null; this.openDetail = null; return; }
      this.open = id;
      this.openDetail = await SB.api(`/api/playlists/${id}`);
    }
  }
};
