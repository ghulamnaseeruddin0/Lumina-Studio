"""Timeline math shared by audio, captions and renderer, so picture and sound always stay in sync."""

INTRO_SECONDS = 3.0
OUTRO_SECONDS = 5.0


def timing(job):
    """Seconds of silence before/after narration in each scene, and the dissolve length between scenes."""
    if job.is_short:
        lead, tail, trans = 0.10, 0.35, 0.25
    else:
        lead, tail, trans = 0.20, 0.55, 0.45
    if not job.cfg.get("render.transitions", True):
        trans = 0.0
    return lead, tail, trans


def build_timeline(job):
    scenes = job.state["scenes"]
    lead, tail, T = timing(job)
    with_cards = not job.is_short
    sections, order = {}, []
    for sc in scenes:
        sections.setdefault(sc["section"], []).append(sc)
        if sc["section"] not in order:
            order.append(sc["section"])
    cursor = INTRO_SECONDS if with_cards else 0.0
    out_scenes, out_sections = [], []
    for si, sec in enumerate(order):
        members = sections[sec]
        t_local, rows = 0.0, []
        for k, sc in enumerate(members):
            dur = float(sc.get("duration_override") or sc["voice"]["duration"])
            is_last_scene = (si == len(order) - 1 and k == len(members) - 1)
            length = lead + dur + tail + (0.7 if is_last_scene else 0.0)
            tr = T if (len(members) > 1) else 0.0
            rows.append({"id": sc["id"], "section": sec, "local_start": t_local, "len": length,
                         "voice_dur": dur, "voice_local": t_local + lead})
            t_local += length - (tr if k < len(members) - 1 else 0.0)
        sec_len = t_local
        for r in rows:
            r["start"] = cursor + r["local_start"]
            r["voice_start"] = cursor + r["voice_local"]
            out_scenes.append(r)
        out_sections.append({"index": sec, "start": cursor, "len": sec_len, "scene_ids": [r["id"] for r in rows],
                             "transition": T if len(members) > 1 else 0.0,
                             "heading": (job.state["script"]["sections"][sec]["heading"]
                                         if sec < len(job.state["script"]["sections"]) else "")})
        cursor += sec_len
    outro_start = cursor
    if with_cards:
        cursor += OUTRO_SECONDS
    return {"lead": lead, "tail": tail, "T": T, "scenes": out_scenes, "sections": out_sections,
            "intro": {"start": 0.0, "len": INTRO_SECONDS} if with_cards else None,
            "outro": {"start": outro_start, "len": OUTRO_SECONDS} if with_cards else None,
            "total": cursor}
