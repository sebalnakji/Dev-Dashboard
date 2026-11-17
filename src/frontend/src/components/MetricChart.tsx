import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { clockText } from '../format'

export interface Series {
  key: string
  name: string
  color: string
}

export interface ChartRow {
  ts: number
  [key: string]: number | null
}

/**
 * 시계열 선 그래프 하나.
 *
 * 애니메이션을 끈다. 5초마다 데이터를 다시 받아 통째로 그리는데,
 * 그때마다 선이 새로 자라나면 읽기 어렵다.
 */
export function MetricChart({
  title,
  rows,
  series,
  domain,
  format,
  tooltipFormat,
}: {
  title: string
  rows: ChartRow[]
  series: Series[]
  /** 퍼센트 지표는 0~100 으로 고정한다. 값에 맞춰 늘리면 작은 흔들림이 커 보인다. */
  domain?: [number, number]
  format: (value: number) => string
  /** 축과 다른 값을 보여주고 싶을 때. 메모리는 퍼센트로 그리고 여기서 실제 용량을 보여준다. */
  tooltipFormat?: (value: number, row: ChartRow) => string
}) {
  return (
    <section className="rounded-xl border border-[var(--color-edge)] bg-[var(--color-panel)] p-4">
      <header className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-200">{title}</h3>
        <div className="flex gap-3 text-xs text-gray-400">
          {series.map((s) => (
            <span key={s.key} className="flex items-center gap-1.5">
              <span className="h-0.5 w-3 rounded" style={{ backgroundColor: s.color }} />
              {s.name}
            </span>
          ))}
        </div>
      </header>

      {rows.length < 2 ? (
        <p className="py-10 text-center text-sm text-gray-600">데이터를 모으는 중…</p>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
            <CartesianGrid stroke="#262b36" vertical={false} />
            <XAxis
              dataKey="ts"
              tickFormatter={clockText}
              stroke="#4b5563"
              tick={{ fontSize: 11 }}
              minTickGap={48}
            />
            <YAxis
              domain={domain ?? [0, 'auto']}
              tickFormatter={format}
              stroke="#4b5563"
              tick={{ fontSize: 11 }}
              width={64}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#171a21',
                border: '1px solid #262b36',
                borderRadius: 8,
                fontSize: 12,
              }}
              // Recharts 는 값을 느슨한 타입으로 넘겨준다. 우리 데이터는 숫자뿐이라 좁혀 쓴다.
              labelFormatter={(label) => clockText(Number(label))}
              formatter={(value, name, item) => {
                const row = (item as { payload?: ChartRow }).payload
                const numeric = Number(value)
                return [
                  tooltipFormat && row ? tooltipFormat(numeric, row) : format(numeric),
                  String(name),
                ]
              }}
            />
            {series.map((s) => (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.name}
                stroke={s.color}
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </section>
  )
}
