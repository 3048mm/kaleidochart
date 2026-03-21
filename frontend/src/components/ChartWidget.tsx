import { useEffect, useRef } from 'react'
import {
    createChart,
    IChartApi,
    ISeriesApi,
    CandlestickData,
    LineData,
    HistogramData,
    ColorType,
    CrosshairMode,
} from 'lightweight-charts'
import { ChartDataPoint } from '../types'

interface ChartWidgetProps {
    data: ChartDataPoint[]
    showSma21: boolean
    showSma50: boolean
    showSma200: boolean
    showEma21: boolean
    showEma50: boolean
    showVolume: boolean
    onCrosshairMove?: (point: ChartDataPoint | null) => void
}

export default function ChartWidget({
    data,
    showSma21,
    showSma50,
    showSma200,
    showEma21,
    showEma50,
    showVolume,
    onCrosshairMove,
}: ChartWidgetProps) {
    const containerRef = useRef<HTMLDivElement>(null)
    const chartRef = useRef<IChartApi | null>(null)
    const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
    const volRef = useRef<ISeriesApi<'Histogram'> | null>(null)
    const sma21Ref = useRef<ISeriesApi<'Line'> | null>(null)
    const sma50Ref = useRef<ISeriesApi<'Line'> | null>(null)
    const sma200Ref = useRef<ISeriesApi<'Line'> | null>(null)
    const ema21Ref = useRef<ISeriesApi<'Line'> | null>(null)
    const ema50Ref = useRef<ISeriesApi<'Line'> | null>(null)
    // Map time string -> data for crosshair lookup
    const dataMapRef = useRef<Map<string, ChartDataPoint>>(new Map())

    // Initialize chart once
    useEffect(() => {
        if (!containerRef.current) return

        const chart = createChart(containerRef.current, {
            layout: {
                background: { type: ColorType.Solid, color: 'transparent' },
                textColor: '#8b9cc8',
                fontFamily: 'Inter, system-ui, sans-serif',
                fontSize: 11,
            },
            grid: {
                vertLines: { color: 'rgba(99, 120, 180, 0.08)' },
                horzLines: { color: 'rgba(99, 120, 180, 0.08)' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
                vertLine: { color: 'rgba(59, 130, 246, 0.5)', width: 1, style: 1 },
                horzLine: { color: 'rgba(59, 130, 246, 0.5)', width: 1, style: 1 },
            },
            rightPriceScale: {
                borderColor: 'rgba(99, 120, 180, 0.18)',
                textColor: '#8b9cc8',
            },
            timeScale: {
                borderColor: 'rgba(99, 120, 180, 0.18)',
                timeVisible: true,
            },
        })

        chartRef.current = chart

        // Candlestick series
        candleRef.current = chart.addCandlestickSeries({
            upColor: '#22d3a0',
            downColor: '#f43f5e',
            borderUpColor: '#22d3a0',
            borderDownColor: '#f43f5e',
            wickUpColor: '#22d3a0',
            wickDownColor: '#f43f5e',
        })

        // Volume histogram (in pane 0 with low opacity)
        volRef.current = chart.addHistogramSeries({
            color: 'rgba(99, 120, 180, 0.3)',
            priceFormat: { type: 'volume' },
            priceScaleId: 'vol',
        })
        chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })

        // Indicator lines
        sma21Ref.current = chart.addLineSeries({ color: '#3b82f6', lineWidth: 1, title: 'SMA21', lastValueVisible: false })
        sma50Ref.current = chart.addLineSeries({ color: '#f59e0b', lineWidth: 1, title: 'SMA50', lastValueVisible: false })
        sma200Ref.current = chart.addLineSeries({ color: '#f43f5e', lineWidth: 1, title: 'SMA200', lastValueVisible: false })
        ema21Ref.current = chart.addLineSeries({ color: '#22d3a0', lineWidth: 1, lineStyle: 1, title: 'EMA21', lastValueVisible: false })
        ema50Ref.current = chart.addLineSeries({ color: '#a855f7', lineWidth: 1, lineStyle: 1, title: 'EMA50', lastValueVisible: false })

        // Crosshair subscription
        chart.subscribeCrosshairMove(param => {
            if (!onCrosshairMove) return
            if (!param.time || !param.point) {
                onCrosshairMove(null)
                return
            }
            // param.time is the string 'YYYY-MM-DD'
            const timeStr = String(param.time)
            const found = dataMapRef.current.get(timeStr)
            onCrosshairMove(found ?? null)
        })

        // Resize observer
        const observer = new ResizeObserver(() => {
            if (containerRef.current) {
                chart.applyOptions({
                    width: containerRef.current.clientWidth,
                    height: containerRef.current.clientHeight,
                })
            }
        })
        observer.observe(containerRef.current)

        return () => {
            observer.disconnect()
            chart.remove()
            chartRef.current = null
        }
    }, [onCrosshairMove]) // Add onCrosshairMove to dependencies to ensure subscription updates if the prop changes

    // Update data when props change
    useEffect(() => {
        if (!data.length) return

        // Rebuild lookup map
        const map = new Map<string, ChartDataPoint>()
        data.forEach(d => map.set(d.time, d))
        dataMapRef.current = map

        const candles: CandlestickData[] = data.map(d => ({
            time: d.time as unknown as CandlestickData['time'],
            open: d.open, high: d.high, low: d.low, close: d.close,
        }))

        const volumes: HistogramData[] = data.map(d => ({
            time: d.time as unknown as HistogramData['time'],
            value: d.volume,
            color: d.close >= d.open ? 'rgba(34, 211, 160, 0.25)' : 'rgba(244, 63, 94, 0.25)',
        }))

        candleRef.current?.setData(candles)
        volRef.current?.setData(volumes)

        const toLine = (key: keyof ChartDataPoint): LineData[] =>
            data
                .filter(d => d[key] != null)
                .map(d => ({ time: d.time as unknown as LineData['time'], value: d[key] as number }))

        sma21Ref.current?.setData(toLine('sma_21'))
        sma50Ref.current?.setData(toLine('sma_50'))
        sma200Ref.current?.setData(toLine('sma_200'))
        ema21Ref.current?.setData(toLine('ema_21'))
        ema50Ref.current?.setData(toLine('ema_50'))

        chartRef.current?.timeScale().fitContent()
    }, [data])

    // Toggle indicators visibility
    useEffect(() => { sma21Ref.current?.applyOptions({ visible: showSma21 }) }, [showSma21])
    useEffect(() => { sma50Ref.current?.applyOptions({ visible: showSma50 }) }, [showSma50])
    useEffect(() => { sma200Ref.current?.applyOptions({ visible: showSma200 }) }, [showSma200])
    useEffect(() => { ema21Ref.current?.applyOptions({ visible: showEma21 }) }, [showEma21])
    useEffect(() => { ema50Ref.current?.applyOptions({ visible: showEma50 }) }, [showEma50])
    useEffect(() => { volRef.current?.applyOptions({ visible: showVolume }) }, [showVolume])

    return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}
