import { describe, it, expect } from 'vitest'
import { StructurePivot } from '../../types'
import { buildStructureMarkers, buildStructureSegments } from '../structurePivot'

const make = (over: Partial<StructurePivot>): StructurePivot => ({
    length: 3,
    ll_date: '2026-04-01', ll_price: 100,
    hl_date: '2026-04-10', hl_price: 105,
    pivot_date: '2026-04-05', pivot_price: 120,
    confirmed_date: '2026-04-13', end_date: '2026-04-20',
    invalidated: false, is_current: false, broken_at_confirmation: false,
    ...over,
})

const timesOf = (...dates: string[]) => new Set(dates)

const ALL_DATES = timesOf(
    '2026-04-01', '2026-04-05', '2026-04-10', '2026-04-13', '2026-04-20', '2026-04-25')

describe('buildStructureSegments', () => {
    it('現在の構造は LL→HL とピボット水準の2本になる', () => {
        const segments = buildStructureSegments([make({ is_current: true })], ALL_DATES)

        expect(segments.map(s => s.role)).toEqual(['current-structure', 'current-pivot'])
        expect(segments[0]).toMatchObject({ from: '2026-04-01', fromValue: 100, to: '2026-04-10', toValue: 105 })
        expect(segments[1]).toMatchObject({ from: '2026-04-05', fromValue: 120, to: '2026-04-20', toValue: 120 })
    })

    it('履歴はピボット水準だけを残す（構造線は引かない）', () => {
        const segments = buildStructureSegments([make({ is_current: false })], ALL_DATES)

        expect(segments).toHaveLength(1)
        expect(segments[0].role).toBe('history-pivot')
    })

    it('両端の日付がチャートに無い線分は捨てる', () => {
        // end_date だけがローソク足の期間外
        const segments = buildStructureSegments(
            [make({ is_current: true, end_date: '2099-01-01' })], ALL_DATES)

        expect(segments.map(s => s.role)).toEqual(['current-structure'])
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
