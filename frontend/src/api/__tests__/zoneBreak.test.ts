import { describe, it, expect } from 'vitest'
import { ZoneBreakFvg, ZoneBreakLevel } from '../../types'
import { buildZoneBreakFvgSegments, buildZoneBreakLevelSegments } from '../zoneBreak'

const makeLevel = (over: Partial<ZoneBreakLevel>): ZoneBreakLevel => ({
    kind: 'ssl',
    price: 100,
    start_date: '2026-04-01',
    end_date: '2026-04-10',
    is_current: false,
    ...over,
})

const makeFvg = (over: Partial<ZoneBreakFvg>): ZoneBreakFvg => ({
    kind: 'bull',
    left_date: '2026-04-01',
    right_date: '2026-04-05',
    top: 105,
    bottom: 100,
    invalidated: false,
    is_current: false,
    ...over,
})

const ALL_DATES = ['2026-04-01', '2026-04-05', '2026-04-10', '2026-04-13', '2026-04-20', '2026-04-25']

describe('buildZoneBreakLevelSegments', () => {
    it('現在のSSL/BSLは実線として引かれる', () => {
        const segments = buildZoneBreakLevelSegments(
            [makeLevel({ kind: 'ssl', price: 90, start_date: '2026-04-10', end_date: '2026-04-20', is_current: true })],
            [makeLevel({ kind: 'bsl', price: 120, start_date: '2026-04-13', end_date: '2026-04-25', is_current: true })],
            ALL_DATES, false,
        )

        expect(segments.map(s => s.role)).toEqual(['current-ssl', 'current-bsl'])
        expect(segments[0]).toMatchObject({ from: '2026-04-10', fromValue: 90, to: '2026-04-20', toValue: 90 })
        expect(segments[1]).toMatchObject({ from: '2026-04-13', fromValue: 120, to: '2026-04-25', toValue: 120 })
        expect(segments[0].color).not.toBe(segments[1].color)
    })

    it('showHistory=false のときは過去の区間を描かない', () => {
        const segments = buildZoneBreakLevelSegments(
            [makeLevel({ is_current: false })], [], ALL_DATES, false)

        expect(segments).toEqual([])
    })

    it('showHistory=true のときは過去の区間を薄い点線で描く', () => {
        const segments = buildZoneBreakLevelSegments(
            [makeLevel({ is_current: false })], [], ALL_DATES, true)

        expect(segments).toHaveLength(1)
        expect(segments[0].role).toBe('history-level')
    })

    it('両端の日付がチャートに無い区間は捨てる', () => {
        const segments = buildZoneBreakLevelSegments(
            [makeLevel({ is_current: true, end_date: '2099-01-01' })], [], ALL_DATES, false)

        expect(segments).toEqual([])
    })

    it('レベルが無ければ何も返さない', () => {
        expect(buildZoneBreakLevelSegments([], [], ALL_DATES, true)).toEqual([])
    })
})

describe('buildZoneBreakFvgSegments', () => {
    it('1つのFVGボックスは上端・下端の2本の線分になる', () => {
        const segments = buildZoneBreakFvgSegments([makeFvg({})], ALL_DATES)

        expect(segments.map(s => s.role)).toEqual(['fvg-top', 'fvg-bottom'])
        expect(segments[0]).toMatchObject({ from: '2026-04-01', fromValue: 105, to: '2026-04-05', toValue: 105 })
        expect(segments[1]).toMatchObject({ from: '2026-04-01', fromValue: 100, to: '2026-04-05', toValue: 100 })
    })

    it('無効化されたFVGは薄い色・点線になる', () => {
        const [valid] = buildZoneBreakFvgSegments([makeFvg({ invalidated: false })], ALL_DATES)
        const [invalidated] = buildZoneBreakFvgSegments([makeFvg({ invalidated: true })], ALL_DATES)

        expect(valid.style).not.toBe(invalidated.style)
        expect(valid.color).not.toBe(invalidated.color)
    })

    it('bull/bearで色が異なる', () => {
        const [bull] = buildZoneBreakFvgSegments([makeFvg({ kind: 'bull' })], ALL_DATES)
        const [bear] = buildZoneBreakFvgSegments([makeFvg({ kind: 'bear' })], ALL_DATES)

        expect(bull.color).not.toBe(bear.color)
    })

    it('現在生きているボックスは太い線になる', () => {
        const [current] = buildZoneBreakFvgSegments([makeFvg({ is_current: true })], ALL_DATES)
        const [past] = buildZoneBreakFvgSegments([makeFvg({ is_current: false })], ALL_DATES)

        expect(current.width).toBeGreaterThan(past.width)
    })

    it('両端の日付がチャートに無いボックスは捨てる', () => {
        const segments = buildZoneBreakFvgSegments(
            [makeFvg({ left_date: '2099-01-01', right_date: '2099-01-05' })], ALL_DATES)

        expect(segments).toEqual([])
    })

    it('上限本数を超えるボックスは新しい方から残す', () => {
        const boxes = ['2026-04-01', '2026-04-05', '2026-04-10'].map(d =>
            makeFvg({ left_date: d, right_date: d }))

        const segments = buildZoneBreakFvgSegments(boxes, ALL_DATES, 2)

        // 2ボックス x 2本(top/bottom) = 4本、最新2ボックス分だけ残る
        expect(segments).toHaveLength(4)
        expect(segments.every(s => s.from === '2026-04-05' || s.from === '2026-04-10')).toBe(true)
    })

    it('ボックスが無ければ何も返さない', () => {
        expect(buildZoneBreakFvgSegments([], ALL_DATES)).toEqual([])
    })
})
