"""
Engagement Analyzer Module.

Detects the most engaging segments of a YouTube video using multiple strategies:
  1. YouTube "Most Replayed" heatmap data (highest accuracy)
  2. Transcript density analysis (fallback)
  3. Chapter-based heuristic (last resort)

Supports both single-peak and multi-peak detection for multi-clip extraction.
"""

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger("highlight_extractor")


@dataclass
class EngagementPeak:
    """Represents a detected engagement peak in the video."""
    start_time: float
    end_time: float
    score: float
    method: str
    details: str
    rank: int = 1


def _windows_overlap(s1, e1, s2, e2, min_gap=0):
    return s1 < (e2 + min_gap) and s2 < (e1 + min_gap)


def _merge_peaks(existing, new_peaks, clip_duration, min_gap):
    merged = list(existing)
    for peak in new_peaks:
        if not any(_windows_overlap(peak.start_time, peak.end_time,
                                    ep.start_time, ep.end_time, min_gap) for ep in merged):
            merged.append(peak)
    return merged


def _fmt(seconds):
    from modules.utils import seconds_to_timestamp
    return seconds_to_timestamp(seconds)


def analyze_heatmap_multi(video_info, clip_duration=60.0, max_clips=5, min_gap=30.0):
    heatmap = video_info.get("heatmap")
    if not heatmap or not isinstance(heatmap, list):
        return []

    logger.info(f"📊 Heatmap data found: {len(heatmap)} data points")
    video_duration = video_info.get("duration", 0)
    if video_duration <= 0:
        return []

    half_clip = clip_duration / 2.0
    windows = []

    for point in heatmap:
        center = (point.get("start_time", 0) + point.get("end_time", 0)) / 2.0
        ws = max(0, center - half_clip)
        we = min(video_duration, center + half_clip)
        if ws == 0:
            we = min(clip_duration, video_duration)
        if we == video_duration:
            ws = max(0, video_duration - clip_duration)

        score = 0
        pv = 0
        pt = center
        for hp in heatmap:
            hc = (hp.get("start_time", 0) + hp.get("end_time", 0)) / 2.0
            val = hp.get("value", 0)
            if ws <= hc <= we:
                score += val
                if val > pv:
                    pv = val
                    pt = hc
        windows.append({"start": ws, "end": we, "score": score, "pv": pv, "pt": pt})

    windows.sort(key=lambda w: w["score"], reverse=True)
    avg_val = sum(p.get("value", 0) for p in heatmap) / max(len(heatmap), 1)

    selected = []
    for w in windows:
        if len(selected) >= max_clips:
            break
        if not any(_windows_overlap(w["start"], w["end"], s["start"], s["end"], min_gap) for s in selected):
            selected.append(w)

    peaks = []
    for i, w in enumerate(selected):
        prom = w["pv"] / avg_val if avg_val > 0 else 1.0
        conf = min(1.0, prom / 3.0)
        peaks.append(EngagementPeak(
            start_time=round(w["start"], 1), end_time=round(w["end"], 1),
            score=round(conf, 2), method="YouTube Most Replayed Heatmap",
            details=f"Peak at {_fmt(w['pt'])} (intensity={w['pv']:.4f}, {prom:.1f}x avg, wscore={w['score']:.2f})",
            rank=i + 1,
        ))
    return peaks


def analyze_transcript_multi(video_id, video_duration, clip_duration=60.0, max_clips=5, min_gap=30.0, existing=None):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return []

    try:
        api = YouTubeTranscriptApi()
        entries = None
        for langs in [('en',), ('en-US', 'en-GB'), ()]:
            try:
                if langs:
                    entries = api.fetch(video_id, languages=langs)
                else:
                    for t in api.list(video_id):
                        entries = t.fetch()
                        break
                if entries:
                    break
            except Exception:
                continue
        if not entries:
            return []
        entries = list(entries)
        if not entries:
            return []
        logger.info(f"📝 Transcript loaded: {len(entries)} segments")
    except Exception:
        return []

    if video_duration <= 0:
        return []

    existing = existing or []
    kw = re.compile(r'\b(wow|amazing|incredible|insane|crazy|unbelievable|oh my|look at|watch this|breaking|important|key|crucial|secret|hack|tip|best part|highlight|moment|dramatic|shocking|surprise|reveal)\b', re.IGNORECASE)

    step, half = 10.0, clip_duration / 2.0
    scored = []
    c = half
    while c <= video_duration - half:
        ws, we = c - half, c + half
        if any(_windows_overlap(ws, we, ep.start_time, ep.end_time, min_gap) for ep in existing):
            c += step
            continue
        wc = sum(len(e.text.split()) for e in entries if ws <= e.start + e.duration / 2 <= we)
        kh = sum(len(kw.findall(e.text)) for e in entries if ws <= e.start + e.duration / 2 <= we)
        sc = sum(1 for e in entries if ws <= e.start + e.duration / 2 <= we)
        total = wc / clip_duration + kh * 2.0 + sc / clip_duration * 10 if clip_duration > 0 else 0
        if total > 0:
            scored.append({"start": ws, "end": we, "center": c, "score": total})
        c += step

    scored.sort(key=lambda w: w["score"], reverse=True)
    peaks = []
    for w in scored:
        if len(peaks) >= max_clips:
            break
        if not any(_windows_overlap(w["start"], w["end"], p.start_time, p.end_time, min_gap) for p in peaks):
            peaks.append(EngagementPeak(
                start_time=round(w["start"], 1), end_time=round(w["end"], 1),
                score=round(min(0.6, w["score"] / 20.0), 2), method="Transcript Density Analysis",
                details=f"Peak at {_fmt(w['center'])} (score={w['score']:.1f})",
            ))
    return peaks


def analyze_chapters_multi(video_info, clip_duration=60.0, max_clips=5, min_gap=30.0, existing=None):
    chapters = video_info.get("chapters")
    if not chapters or len(chapters) < 2:
        return []
    vd = video_info.get("duration", 0)
    if vd <= 0:
        return []
    existing = existing or []
    kw = re.compile(r'(climax|reveal|best|key|important|main|highlight|peak|top|result|conclusion|final|answer|solution|epic|insane|moment|twist|surprise|secret)', re.IGNORECASE)

    scored = []
    for i, ch in enumerate(chapters):
        cs, ce = ch.get("start_time", 0), ch.get("end_time", cs + 60 if 'cs' in dir() else ch.get("start_time", 0) + 60)
        ct = ch.get("title", "")
        cm = (cs + ce) / 2.0
        ps = max(0, 1.0 - abs(cm / vd - 0.65) * 2) if vd > 0 else 0.5
        ts = min(1.0, len(kw.findall(ct)) * 0.5)
        if i == 0 or i == len(chapters) - 1:
            ps *= 0.3
        total = ps * 0.5 + ts * 0.35 + min(1.0, (ce - cs) / 120.0) * 0.15
        half = clip_duration / 2.0
        s, e = max(0, cm - half), min(vd, cm + half)
        if not any(_windows_overlap(s, e, ep.start_time, ep.end_time, min_gap) for ep in existing):
            scored.append({"start": s, "end": e, "score": total, "title": ct, "cs": cs, "ce": ce})

    scored.sort(key=lambda c: c["score"], reverse=True)
    peaks = []
    for ch in scored:
        if len(peaks) >= max_clips:
            break
        if not any(_windows_overlap(ch["start"], ch["end"], p.start_time, p.end_time, min_gap) for p in peaks):
            peaks.append(EngagementPeak(
                start_time=round(ch["start"], 1), end_time=round(ch["end"], 1),
                score=round(min(0.45, ch["score"] * 0.45), 2), method="Chapter-Based Heuristic",
                details=f"Chapter: '{ch['title']}' at {_fmt(ch['cs'])}-{_fmt(ch['ce'])}",
            ))
    return peaks


def _generate_distributed_peaks(vd, clip_duration, count, existing, min_gap):
    if count <= 0:
        return []
    margin = max(clip_duration, vd * 0.05)
    usable = vd - 2 * margin
    if usable <= 0:
        usable, margin = vd, 0
    half = clip_duration / 2.0
    peaks = []
    for i in range(count * 3):
        pos = margin + usable * (i + 1) / (count * 3 + 1)
        s, e = max(0, pos - half), min(vd, pos + half)
        if any(_windows_overlap(s, e, ep.start_time, ep.end_time, min_gap) for ep in existing):
            continue
        if any(_windows_overlap(s, e, p.start_time, p.end_time, min_gap) for p in peaks):
            continue
        peaks.append(EngagementPeak(
            start_time=round(s, 1), end_time=round(e, 1), score=0.15,
            method="Position-Based Fallback",
            details=f"Distributed position at {_fmt(pos)}",
        ))
        if len(peaks) >= count:
            break
    return peaks


def analyze_fallback(video_info, clip_duration=60.0):
    vd = video_info.get("duration", 300)
    center = vd * 0.65
    half = clip_duration / 2.0
    s, e = max(0, center - half), min(vd, center + half)
    if e - s < clip_duration * 0.8:
        if s == 0:
            e = min(vd, clip_duration)
        else:
            s = max(0, e - clip_duration)
    return EngagementPeak(start_time=round(s, 1), end_time=round(e, 1), score=0.15,
                          method="Position-Based Fallback (65% mark)",
                          details="No engagement data available. Using statistical 65% position heuristic.")


def find_multiple_peaks(video_info, video_id, clip_duration=60.0, min_clips=3, max_clips=5, min_gap=30.0):
    """Detect multiple non-overlapping engagement peaks."""
    vd = video_info.get("duration", 0)
    if vd <= 0:
        return [analyze_fallback(video_info, clip_duration)]
    if clip_duration > vd:
        clip_duration = vd

    peaks = []

    logger.info("🔍 Trying: Heatmap Multi-Peak Analysis...")
    hp = analyze_heatmap_multi(video_info, clip_duration, max_clips, min_gap)
    if hp:
        logger.info(f"✅ Heatmap found {len(hp)} peaks")
        peaks = hp

    if len(peaks) < min_clips:
        logger.info("🔍 Trying: Transcript Multi-Peak Analysis...")
        tp = analyze_transcript_multi(video_id, vd, clip_duration, max_clips, min_gap, peaks)
        if tp:
            logger.info(f"📝 Transcript found {len(tp)} additional peaks")
            peaks = _merge_peaks(peaks, tp, clip_duration, min_gap)

    if len(peaks) < min_clips:
        logger.info("🔍 Trying: Chapter-Based Multi-Peak Analysis...")
        cp = analyze_chapters_multi(video_info, clip_duration, max_clips, min_gap, peaks)
        if cp:
            logger.info(f"📑 Chapters found {len(cp)} additional peaks")
            peaks = _merge_peaks(peaks, cp, clip_duration, min_gap)

    if len(peaks) < min_clips:
        logger.info(f"🎯 Filling with position-based fallback ({len(peaks)}/{min_clips})...")
        fp = _generate_distributed_peaks(vd, clip_duration, min_clips - len(peaks), peaks, min_gap)
        peaks = _merge_peaks(peaks, fp, clip_duration, min_gap)

    peaks = sorted(peaks, key=lambda p: p.score, reverse=True)[:max_clips]
    peaks = sorted(peaks, key=lambda p: p.start_time)
    for rank, p in enumerate(sorted(peaks, key=lambda p: p.score, reverse=True), 1):
        p.rank = rank
    peaks = sorted(peaks, key=lambda p: p.start_time)

    logger.info(f"🎬 Final: {len(peaks)} clips selected")
    return peaks


def find_peak_engagement(video_info, video_id, clip_duration=60.0):
    """Single peak detection (backward compatible)."""
    vd = video_info.get("duration", 0)
    if vd > 0 and clip_duration > vd:
        clip_duration = vd

    for name, fn in [
        ("Heatmap Analysis", lambda: analyze_heatmap_multi(video_info, clip_duration, 1)[0] if analyze_heatmap_multi(video_info, clip_duration, 1) else None),
        ("Transcript Analysis", lambda: analyze_transcript_multi(video_id, vd, clip_duration, 1)[0] if analyze_transcript_multi(video_id, vd, clip_duration, 1) else None),
        ("Chapter Analysis", lambda: analyze_chapters_multi(video_info, clip_duration, 1)[0] if analyze_chapters_multi(video_info, clip_duration, 1) else None),
    ]:
        logger.info(f"🔍 Trying: {name}...")
        try:
            result = fn()
            if result:
                logger.info(f"✅ {name} succeeded! Peak: {_fmt(result.start_time)} → {_fmt(result.end_time)} (confidence: {result.score:.0%})")
                return result
            else:
                logger.info(f"⏭️  {name}: no data available, trying next strategy...")
        except Exception as e:
            logger.warning(f"⚠️  {name} failed: {e}")

    logger.info("🎯 Using position-based fallback...")
    return analyze_fallback(video_info, clip_duration)
