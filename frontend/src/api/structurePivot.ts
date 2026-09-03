// frontend/src/api/structurePivot.ts
import { CounterTrend, StructurePivot, StructurePivotResponse } from '../types'

/** 描画する過去の構造ピボットの本数。増やすと線が増えて読みにくくなる */
export const STRUCTURE_HISTORY_LIMIT = 30

const CURRENT_COLOR = '#00bcd4'
const HISTORY_COLOR = 'rgba(255, 255, 255, 0.28)'
// オリジナル版（Advanced Structure Pivot）の配色に寄せる:
//   1st = 黄（早いエントリー） / 2nd = 水色（本ピボット） / TP = 灰（利確目標）
const FIB_1ST_COLOR = '#e0b000'
const TP_COLOR = 'rgba(200, 200, 200, 0.75)'
// カウンタートレンド線は「構造が無いときに出る別カテゴリ」なので、
// 構造側（水色）とも 1st（黄）とも混ざらない色を当てる
const COUNTER_COLOR = '#ff8c42'

/** lightweight-charts の lineStyle（既存コードに合わせて数値で持つ） */
const SOLID = 0
const DOTTED = 1
const DASHED = 2

export type StructureSegmentRole =
    | 'history-pivot'
    | 'current-structure'
    | 'current-pivot'      // オリジナル版の "2nd"
    | 'current-1st'
    | 'current-tp1'
    | 'current-tp2'
    | 'current-counter'

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

/** レスポンス全体を返す。構造だけでなくカウンター線も要るようになったため
 *  （以前は `structures` だけを返して `current` / `counters` を捨てていた）。 */
export async function fetchStructurePivot(
    symbolId: number,
    fullRange: boolean,
): Promise<StructurePivotResponse> {
    const res = await fetch(`/api/chart/${symbolId}/structure_pivot?full_range=${fullRange}`)
    if (!res.ok) throw new Error(`structure_pivot: ${res.status}`)
    const json: StructurePivotResponse = await res.json()
    return {
        ...json,
        structures: json.structures ?? [],
        counters: json.counters ?? [],
        current_counter: json.current_counter ?? null,
    }
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
    dates: string[],
    historyLimit: number = STRUCTURE_HISTORY_LIMIT,
    currentCounter: CounterTrend | null = null,
): StructureSegment[] {
    const times = new Set(dates)
    const lastDate = dates[dates.length - 1]
    const segments: StructureSegment[] = []
    const push = (seg: StructureSegment) => {
        if (times.has(seg.from) && times.has(seg.to)) segments.push(seg)
    }

    // 履歴のピボット水準は**構造が死んだバーで打ち切る**（TradingView 版は右端まで
    // 延長するが、あえて変えている）。現在の構造に 1st / 2nd / TP1 / TP2 の4本を
    // 右端まで引くようになったため、履歴30本まで延長すると線が多すぎて読めない
    // （2026-08-27 にユーザー判断で延長を取りやめ）。
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
        // 2nd（本ピボット）: 太い実線
        push({
            from: s.pivot_date, fromValue: s.pivot_price,
            to: s.end_date, toValue: s.pivot_price,
            color: CURRENT_COLOR, width: 2, style: SOLID, role: 'current-pivot',
        })
        // 1st（fib 0.618 の早いエントリー候補）と TP1 / TP2。
        // いずれも HL から右端まで引く（水準として読むものなので構造の終端で切らない）
        const levels: [number, string, string, StructureSegmentRole][] = [
            [s.fib_1st_price, FIB_1ST_COLOR, 'dashed', 'current-1st'],
            [s.tp1_price, TP_COLOR, 'dashed', 'current-tp1'],
            [s.tp2_price, TP_COLOR, 'dashed', 'current-tp2'],
        ]
        levels.forEach(([value, color, _style, role]) => {
            if (!Number.isFinite(value)) return
            push({
                from: s.hl_date, fromValue: value,
                to: lastDate, toValue: value,
                color, width: 1, style: DASHED, role,
            })
        })
    })

    // カウンタートレンド線（現在の1本だけ）。構造とは排他なので、これが出るときは
    // 上の current-* は1本も無い。アンカー1 から線の終端まで**傾いた線分**として引く
    // ——アンカー2 は傾きを決める点であって終端ではないので、そこで切らない。
    // 履歴は描かない（2026-09-03 ユーザー判断。構造ピボットの履歴と同じく本数が増えて読めなくなるため）
    if (currentCounter) {
        push({
            from: currentCounter.a1_date, fromValue: currentCounter.a1_value,
            to: currentCounter.end_date, toValue: currentCounter.end_value,
            color: COUNTER_COLOR, width: 2, style: SOLID, role: 'current-counter',
        })
    }

    return segments
}

/** 現在生きている構造の LL / HL にだけマーカーを付ける（履歴まで付けると読めなくなる）。 */
export function buildStructureMarkers(
    structures: StructurePivot[],
    dates: string[],
): StructureMarker[] {
    const times = new Set(dates)
    const markers: StructureMarker[] = []
    structures.filter(s => s.is_current).forEach(s => {
        if (times.has(s.ll_date)) markers.push({ time: s.ll_date, text: 'LL', color: CURRENT_COLOR })
        if (times.has(s.hl_date)) markers.push({ time: s.hl_date, text: 'HL', color: CURRENT_COLOR })
    })
    return markers
}
