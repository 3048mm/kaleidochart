import { describe, it, expect } from 'vitest'
import { StructurePivot } from '../../types'
import { buildStructureMarkers, buildStructureSegments } from '../structurePivot'

const make = (over: Partial<StructurePivot>): StructurePivot => ({
    length: 3,
    ll_date: '2026-04-01', ll_price: 100,
    hl_date: '2026-04-10', hl_price: 105,
    pivot_date: '2026-04-05', pivot_price: 120,
    // HL=105 / pivot=120 -> range=15。1st=105+15*0.618, TP1=105+15*1.764, TP2=105+15*2.618
    fib_1st_price: 114.27, tp1_price: 131.46, tp2_price: 144.27,
    confirmed_date: '2026-04-13', end_date: '2026-04-20',
    invalidated: false, is_current: false, broken_at_confirmation: false,
    ...over,
})

/** 順序付きの日付配列。右端（最後の要素）が履歴線の終点になる */
const ALL_DATES = [
    '2026-04-01', '2026-04-05', '2026-04-10', '2026-04-13', '2026-04-20', '2026-04-25']
const LAST_DATE = ALL_DATES[ALL_DATES.length - 1]

describe('buildStructureSegments', () => {
    it('現在の構造は LL→HL・2nd・1st・TP1・TP2 の5本になる', () => {
        const segments = buildStructureSegments([make({ is_current: true })], ALL_DATES)

        expect(segments.map(s => s.role)).toEqual(
            ['current-structure', 'current-pivot', 'current-1st', 'current-tp1', 'current-tp2'])
        expect(segments[0]).toMatchObject({ from: '2026-04-01', fromValue: 100, to: '2026-04-10', toValue: 105 })
        expect(segments[1]).toMatchObject({ from: '2026-04-05', fromValue: 120, to: '2026-04-20', toValue: 120 })
    })

    it('1st / TP1 / TP2 は HL から右端まで水平に引く', () => {
        const [, , first, tp1, tp2] = buildStructureSegments([make({ is_current: true })], ALL_DATES)

        for (const seg of [first, tp1, tp2]) {
            expect(seg.from).toBe('2026-04-10')      // HL の位置が起点
            expect(seg.to).toBe(LAST_DATE)
            expect(seg.fromValue).toBe(seg.toValue)  // 水平
        }
        expect(first.fromValue).toBeLessThan(tp1.fromValue)
        expect(tp1.fromValue).toBeLessThan(tp2.fromValue)
    })

    it('履歴はピボット水準だけを残す（構造線は引かない）', () => {
        const segments = buildStructureSegments([make({ is_current: false })], ALL_DATES)

        expect(segments).toHaveLength(1)
        expect(segments[0].role).toBe('history-pivot')
    })

    it('履歴のピボット線は構造が死んだバーで打ち切る', () => {
        // 現在の構造の4本（1st/2nd/TP1/TP2）を右端まで引くため、履歴まで延長すると
        // 線が多すぎて読めない。履歴は「いつどこにあったか」が分かれば十分とする。
        const segments = buildStructureSegments(
            [make({ is_current: false, end_date: '2026-04-13' })], ALL_DATES)

        expect(segments[0].to).toBe('2026-04-13')
        expect(segments[0].to).not.toBe(LAST_DATE)
        expect(segments[0].from).toBe('2026-04-05')   // ピボット足の位置が起点
    })

    it('両端の日付がチャートに無い線分は捨てる', () => {
        // end_date だけがローソク足の期間外
        const segments = buildStructureSegments(
            [make({ is_current: true, end_date: '2099-01-01' })], ALL_DATES)

        // 2nd は end_date が期間外なので落ちるが、1st / TP は右端まで引くので残る
        expect(segments.map(s => s.role)).toEqual(
            ['current-structure', 'current-1st', 'current-tp1', 'current-tp2'])
    })

    it('履歴は上限本数まで、新しい方から残す', () => {
        const history = ['2026-04-05', '2026-04-10', '2026-04-13'].map((d, i) =>
            make({ pivot_date: d, pivot_price: 100 + i, end_date: '2026-04-20' }))

        const segments = buildStructureSegments(history, ALL_DATES, 2)

        expect(segments).toHaveLength(2)
        expect(segments.map(s => s.from)).toEqual(['2026-04-10', '2026-04-13'])
    })

    it('構造が無ければ何も返さない', () => {
        expect(buildStructureSegments([], ALL_DATES)).toEqual([])
    })
})

describe('buildStructureMarkers', () => {
    it('現在の構造の LL / HL にだけマーカーを付ける', () => {
        const structures = [make({ is_current: false }), make({ is_current: true })]

        const markers = buildStructureMarkers(structures, ALL_DATES)

        expect(markers.map(m => m.text)).toEqual(['LL', 'HL'])
        expect(markers.map(m => m.time)).toEqual(['2026-04-01', '2026-04-10'])
    })

    it('チャートに無い日付は除く', () => {
        const markers = buildStructureMarkers(
            [make({ is_current: true, ll_date: '2099-01-01' })], ALL_DATES)

        expect(markers.map(m => m.text)).toEqual(['HL'])
    })
})
