"""
Key-driven scrolling for the long list overlays (results 5002, results lite
5003, classic grid 5001 /?style=classic).

2026-10-25, Thomas: no automatic paging or scrolling on any overlay — pages
move only with the Stream Deck page keys. Those keys talk to the grid server
(5001 /nav/next|prev|first, wct_board.PageNav); every page polls that and
applies each command once. Here: next = one screen down (wraps to the top
at the end), prev = one screen up, first = top. The arrow / PageUp /
PageDown / Home keys in the page send the same commands. ?autoscroll=1 on a
source brings back the old endless scroll, ?nav=<url> another nav server.

The page must have #scroll-viewport / #scroll-inner and call
updateAutoScroll() after every redraw; it keeps its old scroller as
oldAutoScroll() for ?autoscroll=1. No Jinja markers in here (the pages go
through render_template_string).
"""

KEY_SCROLL_JS = r"""
// ---- page keys instead of auto-scroll (page_keys.py) ----
const KS_QS = new URLSearchParams(location.search);
const KS_AUTO = KS_QS.get("autoscroll") === "1";
const KS_NAV = (KS_QS.get("nav") || "http://" + (location.hostname || "localhost") + ":5001").replace(/\/$/, "");
let ksOffset = 0, ksSeq = null;
function ksMax() {
    const vp = document.getElementById("scroll-viewport"), inner = document.getElementById("scroll-inner");
    return (vp && inner) ? Math.max(0, inner.scrollHeight - vp.clientHeight) : 0;
}
function ksApply() {
    const inner = document.getElementById("scroll-inner");
    if (!inner) return;
    ksOffset = Math.min(ksOffset, ksMax());
    inner.style.transition = "transform .5s ease";
    inner.style.transform = ksOffset ? "translateY(" + (-ksOffset) + "px)" : "";
}
function ksStep(dir) {
    const vp = document.getElementById("scroll-viewport");
    if (!vp || dir === null) return;
    const max = ksMax(), step = Math.max(40, vp.clientHeight - 40);
    if (dir === 0) ksOffset = 0;
    else if (dir > 0) ksOffset = ksOffset >= max - 2 ? 0 : Math.min(max, ksOffset + step);
    else ksOffset = ksOffset <= 2 ? max : Math.max(0, ksOffset - step);
    ksApply();
}
async function ksPoll() {
    let st;
    try { st = await (await fetch(KS_NAV + "/nav", { cache: "no-store" })).json(); } catch (e) { return; }
    if (ksSeq === null) { ksSeq = st.seq; return; }
    if (st.seq !== ksSeq) {
        ksSeq = st.seq;
        ksStep(st.cmd === "next" ? 1 : st.cmd === "prev" ? -1 : st.cmd === "first" ? 0 : null);
    }
}
function updateAutoScroll() {
    if (KS_AUTO) return oldAutoScroll();
    ksApply();
}
if (!KS_AUTO) {
    setInterval(ksPoll, 300); ksPoll();
    document.addEventListener("keydown", e => {
        const cmd = { ArrowDown: "next", ArrowRight: "next", PageDown: "next",
                      ArrowUp: "prev", ArrowLeft: "prev", PageUp: "prev", Home: "first" }[e.key];
        if (!cmd) return;
        fetch(KS_NAV + "/nav/" + cmd, { method: "POST" }).then(ksPoll).catch(() => {});
        e.preventDefault();
    });
    const ksTop = () => ksStep(0);
    window.addEventListener("obsSourceVisibleChanged", e => { if (!e.detail || e.detail.visible) ksTop(); });
    window.addEventListener("obsSourceActiveChanged", e => { if (!e.detail || e.detail.active) ksTop(); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) ksTop(); });
}
"""
