// frontend/src/api/zoneBreak.ts
import { ZoneBreakFvg, ZoneBreakLevel, ZoneBreakResponse } from '../types'

/** 描画する過去のFVGボックスの本数上限（ペイロード自体はサーバ側で絞らないため、
 *  フロント側で読みやすさのために切る。`structurePivot.ts` の STRUCTURE_HISTORY_LIMIT と同じ考え方）。 */
export const ZONE_BREAK_FVG_HISTORY_LIMIT = 30

const SSL_COLOR = '#26a69a'   // 緑寄り（Pine原文の SSL = color.green に寄せる）
const BSL_COLOR = '#ef5350'   // 赤寄り（Pine原文の BSL = color.maroon に寄せる）
const HISTORY_COLOR = 'rgba(255, 255, 255, 0.28)'
const FVG_BULL_COLOR = 'rgba(38, 166, 154, 0.55)'
const FVG_BEAR_COLOR = 'rgba(239, 83, 80, 0.55)'
const FVG_INVALIDATED_COLOR = 'rgba(255, 255, 255, 0.18)'

const SOLID = 0
const DOTTED = 1

export type ZoneBreakSegmentRole = 'current-ssl' | 'current-bsl' | 'history-level' | 'fvg-top' | 'fvg-bottom'

export interface ZoneBreakSegment {
    from: string
    fromValue: number
    to: string
    toValue: number
    color: string
    width: 1 | 2
    style: number
    role: ZoneBreakSegmentRole
}

export async function fetchZoneBreak(
    symbolId: number,
    fullRange: boolean,
): Promise<ZoneBreakResponse> {
    const res = await fetch(`/api/chart/${symbolId}/zone_break?full_range=${fullRange}`)
    if (!res.ok) throw new Error(`zone_break: ${res.status}`)
    const json: ZoneBreakResponse = await res.json()
    return {
        ...json,
        ssl_levels: json.ssl_levels ?? [],
        bsl_levels: json.bsl_levels ?? [],
        fvg_boxes: json.fvg_boxes ?? [],
    }
}

/**
 * SSL/BSL の水準区間を線分へ変換する。
 *
 * - 現在の区間は色付き実線（SSL=緑寄り, BSL=赤寄り）。
 * - 履歴（過去の区間）は `showHistory` が true のときだけ、薄いグレーの点線で残す
 *   （`structurePivot.ts` の history-pivot と同じ考え方。既定は現在の区間のみ）。
 * - 両端の日付がチャートのデータに存在しない線分は捨てる（lightweight-charts は
 *   データに無い時刻を渡すと描画が壊れるため。`buildStructureSegments` と同じ対策）。
 */
export function buildZoneBreakLevelSegments(
    sslLevels: ZoneBreakLevel[],
    bslLevels: ZoneBreakLevel[],
    dates: string[],
    showHistory: boolean,
): ZoneBreakSegment[] {
    const times = new Set(dates)
    const segments: ZoneBreakSegment[] = []
    const push = (seg: ZoneBreakSegment) => {
        if (times.has(seg.from) && times.has(seg.to)) segments.push(seg)
    }

    const addLevels = (levels: ZoneBreakLevel[], currentColor: string, role: 'current-ssl' | 'current-bsl') => {
        levels.forEach(lv => {
            if (lv.is_current) {
                push({
                    from: lv.start_date, fromValue: lv.price,
                    to: lv.end_date, toValue: lv.price,
                    color: currentColor, width: 2, style: SOLID, role,
                })
            } else if (showHistory) {
                push({
                    from: lv.start_date, fromValue: lv.price,
                    to: lv.end_date, toValue: lv.price,
                    color: HISTORY_COLOR, width: 1, style: DOTTED, role: 'history-level',
                })
            }
        })
    }

    addLevels(sslLevels, SSL_COLOR, 'current-ssl')
    addLevels(bslLevels, BSL_COLOR, 'current-bsl')
    return segments
}

/**
 * FVG（Fair Value Gap）ボックスを、上端・下端の2本の線分へ変換する。
 *
 * lightweight-charts v4 には矩形（塗りつぶしボックス）を直接描画する標準APIが無いため、
 * 既存の「線分の集合」インフラ（構造ピボットの fib 水準等と同じ仕組み）に乗せ、
 * 上端・下端の2本のラインでゾーンの範囲を表す（塗りつぶしではなく輪郭のみ）。
 * doc/in_progress/zone_break_plan.md §4-Q4 で「実装時に技術検証」として保留していた点の
 * 決定（2026-09-13、既存インフラの再利用を優先し矩形塗りつぶしは見送り）。
 */
export function buildZoneBreakFvgSegments(
    fvgBoxes: ZoneBreakFvg[],
    dates: string[],
    historyLimit: number = ZONE_BREAK_FVG_HISTORY_LIMIT,
): ZoneBreakSegment[] {
    const times = new Set(dates)
    const segments: ZoneBreakSegment[] = []
    const push = (seg: ZoneBreakSegment) => {
        if (times.has(seg.from) && times.has(seg.to)) segments.push(seg)
    }

    // 直近のボックスを優先する（現存中のものを削らないよう、末尾から取る）
    fvgBoxes.slice(-historyLimit).forEach(box => {
        const baseColor = box.invalidated
            ? FVG_INVALIDATED_COLOR
            : (box.kind === 'bull' ? FVG_BULL_COLOR : FVG_BEAR_COLOR)
        const width: 1 | 2 = box.is_current ? 2 : 1
        push({
            from: box.left_date, fromValue: box.top,
            to: box.right_date, toValue: box.top,
            color: baseColor, width, style: box.invalidated ? DOTTED : SOLID, role: 'fvg-top',
        })
        push({
            from: box.left_date, fromValue: box.bottom,
            to: box.right_date, toValue: box.bottom,
            color: baseColor, width, style: box.invalidated ? DOTTED : SOLID, role: 'fvg-bottom',
        })
    })

    return segments
}
