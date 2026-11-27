window.SB_VIEWS = window.SB_VIEWS || {};
window.SB_VIEWS["dashboard-view"] = {
  template: `
  <div>
    <div class="panel">
      <h2>总览</h2>
      <p class="lead">{{ meta.skeleton || "加载中…" }}</p>
      <div class="stats">
        <div class="stat" v-for="s in cards" :key="s.k"><b>{{ s.v }}</b><span>{{ s.l }}</span></div>
      </div>
    </div>

    <div class="panel">
      <h2>媒资校验和健康（signboard-13）</h2>
      <p class="lead">“台账可播”只看 assets.status；“账实可播”还要求媒资桥校验一致。两者差值就是未核先 ready 的花屏风险面。</p>
      <div class="media-health">
        <div class="cell"><b>{{ media.total }}</b><span>素材总数</span></div>
        <div class="cell" :class="{ good: media.verified }"><b>{{ media.verified }}</b><span>校验一致</span></div>
        <div class="cell warn"><b>{{ media.unverified }}</b><span>未校验</span></div>
        <div class="cell bad"><b>{{ media.mismatch }}</b><span>校验不一致</span></div>
        <div class="cell good"><b>{{ media.playable }}</b><span>账实可播</span></div>
        <div class="cell warn"><b>{{ media.ledgerReady }}</b><span>台账 ready（可能含假绿）</span></div>
        <div class="cell bad"><b>{{ pl.fakeReady }}</b><span>清单“台账假可播”</span></div>
        <div class="cell" :class="{ good: pl.complete }"><b>{{ pl.complete }}/{{ pl.total }}</b><span>可播齐套清单</span></div>
      </div>
      <p class="hint" style="margin-top:12px">前往「空桩桥」执行媒资桥校验，或在「素材」页逐条核验；互斥名 <code class="ck">媒资桥置可播互斥</code>。</p>
    </div>
  </div>`,
  data() {
    return {
      meta: {},
      cards: [],
      media: { total: 0, verified: 0, unverified: 0, mismatch: 0, playable: 0, ledgerReady: 0 },
      pl: { total: 0, complete: 0, fakeReady: 0 }
    };
  },
  async mounted() {
    const d = await SB.api("/api/dashboard");
    this.meta = d;
    this.cards = [
      { k: "screens", l: "屏幕总数", v: d.screens },
      { k: "online", l: "在线屏幕", v: d.online_screens },
      { k: "assets", l: "台账可播素材", v: d.ready_assets },
      { k: "pubs", l: "正式发布", v: d.official_publications },
      { k: "shadows", l: "待升正影子", v: d.open_shadows },
      { k: "ov", l: "生效覆盖", v: d.active_overrides }
    ];
    try {
      const [assets, playlists] = await Promise.all([
        SB.api("/api/assets"),
        SB.api("/api/playlists")
      ]);
      const a = assets.items || [];
      this.media = {
        total: a.length,
        verified: a.filter(x => x.checksum_status === "verified").length,
        unverified: a.filter(x => (x.checksum_status || "unverified") === "unverified").length,
        mismatch: a.filter(x => x.checksum_status === "mismatch").length,
        playable: a.filter(x => x.playable).length,
        ledgerReady: a.filter(x => x.status === "ready").length
      };
      const p = playlists.items || [];
      this.pl = {
        total: p.length,
        complete: p.filter(x => x.complete).length,
        fakeReady: p.filter(x => x.fake_ready).length
      };
    } catch (e) { /* 总览主卡片仍可用 */ }
  }
};
