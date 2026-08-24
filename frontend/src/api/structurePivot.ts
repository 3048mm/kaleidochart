// frontend/src/api/structurePivot.ts
import { StructurePivot, StructurePivotResponse } from '../types'

/** 描画する過去の構造ピボットの本数。増やすと線が増えて読みにくくなる */
export const STRUCTURE_HISTORY_LIMIT = 30

const CURRENT_COLOR = '#00bcd4'
const HISTORY_COLOR = 'rgba(255, 255, 255, 0.28)'

/** lightweight-charts の lineStyle（既存コードに合わせて数値で持つ） */
const SOLID = 0
const DOTTED = 1
const DASHED = 2

export type StructureSegmentRole = 'history-pivot' | 'current-structure' | 'current-pivot'

export interface StructureSegment {
    from: string
    fromValue: number
    to: string
    toValue: number
    color: string
    width: 1 | 2
    style: number
    role: StructureSegmentRole
}

export interface StructureMarker {
    time: string
    text: 'LL' | 'HL'
    color: string
}

export async function fetchStructurePivot(
    symbolId: number,
    fullRange: boolean,
): Promise<StructurePivot[]> {
    const res = await fetch(`/api/chart/${symbolId}/structure_pivot?full_range=${fullRange}`)
    if (!res.ok) throw new Error(`structure_pivot: ${res.status}`)
    const json: StructurePivotResponse = await res.json()
    return json.structures ?? []
}

/**
 * 構造の配列を、チャートに引く線分の配列へ変換する。
 *
 * - 履歴はピボット水準（＝過去の抵抗線）だけを残す。TV 版も勝者が交代した時点で
 *   構造線を消し、ピボット線だけを履歴へ送る挙動になっている。
 * - 現在の構造は LL→HL の破線とピボット水準の実線の2本。
 * - **両端の日付がチャートのデータに存在しない線分は捨てる。** lightweight-charts は
 *   データに無い時刻を渡すと描画が壊れるため。full_range の切り替え直後など、
 *   構造の取得とローソク足の期間が一時的にずれる場合に効く。
 */
export function buildStructureSegments(
    structures: StructurePivot[],
    times: Set<string>,
    historyLimit: number = STRUCTURE_HISTORY_LIMIT,
): StructureSegment[] {
    const segments: StructureSegment[] = []
    const push = (seg: StructureSegment) => {
        if (times.has(seg.from) && times.has(seg.to)) segments.push(seg)
    }

    structures
        .filter(s => !s.is_current)
        .slice(-historyLimit)
        .forEach(s => push({
            from: s.pivot_date, fromValue: s.pivot_price,
            to: s.end_date, toValue: s.pivot_price,
            color: HISTORY_COLOR, width: 1, style: DOTTED, role: 'history-pivot',
        }))

    structures.filter(s => s.is_current).forEach(s => {
        push({
            from: s.ll_date, fromValue: s.ll_price,
            to: s.hl_date, toValue: s.hl_price,
            color: CURRENT_COLOR, width: 1, style: DASHED, role: 'current-structure',
        })
        push({
            from: s.pivot_date, fromValue: s.pivot_price,
            to: s.end_date, toValue: s.pivot_price,
            color: CURRENT_COLOR, width: 2, style: SOLID, role: 'current-pivot',
        })
    })

    return segments
}

/** 現在生きている構造の LL / HL にだけマーカーを付ける（履歴まで付けると読めなくなる）。 */
export function buildStructureMarkers(
    structures: StructurePivot[],
    times: Set<string>,
): StructureMarker[] {
    const markers: StructureMarker[] = []
    structures.filter(s => s.is_current).forEach(s => {
        if (times.has(s.ll_date)) markers.push({ time: s.ll_date, text: 'LL', color: CURRENT_COLOR })
        if (times.has(s.hl_date)) markers.push({ time: s.hl_date, text: 'HL', color: CURRENT_COLOR })
    })
    return markers
}
