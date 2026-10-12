<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { formatCompact, formatNumber } from '@/utils/format'

// 按需注册：全量 import 'echarts' 会把整个库（含地图、关系图这些用不到的）打进包里，
// 用到哪几个就注册哪几个
echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = withDefaults(
  defineProps<{
    labels: string[]
    values: number[]
    name: string
    formatValue?: (value: number) => string
    formatAxis?: (value: number) => string
    type?: 'line' | 'bar'
    height?: string
  }>(),
  {
    type: 'line',
    height: '260px',
    formatValue: (value: number) => formatNumber(value),
    formatAxis: (value: number) => formatCompact(value),
  },
)

const container = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

// canvas 不认 CSS 变量，颜色只能读出来再用。读而不是抄一份写死在这里：
// 抄的话改配色要改两处，而漏掉的那一处不会报错，只会在页面上难看
function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

// 轴上的标签要短：按天去掉年份只留 10-06，按小时只留 14:00
function shortLabel(value: string): string {
  if (value.includes('T')) return value.slice(11, 16)
  return value.length === 10 ? value.slice(5) : value
}

// tooltip 不比轴拥挤，把日期补全更好认
function fullLabel(value: string): string {
  return value.includes('T') ? value.replace('T', ' ') : value
}

// ECharts 没有从 core 导出这个回调的参数类型，只声明这里用得到的两个字段
interface TooltipParam {
  axisValue: string
  value: number | string
}

function buildOption(): echarts.EChartsCoreOption {
  const green = token('--green-500')

  return {
    grid: { top: 16, right: 14, bottom: 26, left: 56 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: token('--line'),
      borderWidth: 1,
      padding: [9, 13],
      textStyle: { color: token('--text'), fontSize: 13 },
      axisPointer: { type: 'line', lineStyle: { color: token('--green-200') } },
      formatter: (params: TooltipParam | TooltipParam[]) => {
        const first = Array.isArray(params) ? params[0] : params
        if (!first) return ''
        return `${fullLabel(first.axisValue)}<br/>${props.name}：${props.formatValue(Number(first.value))}`
      },
    },
    xAxis: {
      type: 'category',
      data: props.labels,
      // 柱状图要靠它把两根柱子分开，折线图贴边更好看
      boundaryGap: props.type === 'bar',
      axisLine: { lineStyle: { color: token('--line') } },
      axisTick: { show: false },
      axisLabel: {
        color: token('--faint'),
        fontSize: 11,
        formatter: shortLabel,
      },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: token('--line') } },
      axisLabel: {
        color: token('--faint'),
        fontSize: 11,
        // 轴刻度默认是唯一用缩写的地方：完整数字（11,391,234）会把左边距吃到
        // 十几厘米宽，网格区域被挤没。轴读量级就够，精确值有 tooltip。
        // 成本图要带 ¥ 前缀，所以留了 formatAxis 给调用方换一套
        formatter: (value: number) => props.formatAxis(value),
      },
    },
    series: [
      {
        type: props.type,
        name: props.name,
        data: props.values,
        // 近 7 天只有 7 根柱子，不限宽的话每根能摊到一百多像素，看着像色块不像柱状图
        barMaxWidth: 36,
        // 点太密时会糊成一条边，超过一个月就不画点了
        showSymbol: props.values.length <= 31,
        symbol: 'circle',
        symbolSize: 6,
        itemStyle: { color: green, borderRadius: 3 },
        lineStyle: { width: 2, color: green },
        areaStyle:
          props.type === 'line'
            ? {
                color: {
                  type: 'linear',
                  x: 0,
                  y: 0,
                  x2: 0,
                  y2: 1,
                  colorStops: [
                    { offset: 0, color: 'rgba(64, 145, 108, 0.16)' },
                    { offset: 1, color: 'rgba(64, 145, 108, 0)' },
                  ],
                },
              }
            : undefined,
      },
    ],
  }
}

onMounted(() => {
  if (!container.value) return

  chart = echarts.init(container.value)
  chart.setOption(buildOption())

  // 监听容器而不是 window：侧边栏折叠、窗口缩放之外的变化它也能接住
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(container.value)
})

// 数据变了就换 option，不重新 init：重建实例会把 resize 监听和动画都丢掉
watch(
  () => [props.labels, props.values, props.type],
  () => chart?.setOption(buildOption()),
)

onBeforeUnmount(() => {
  // 不 dispose 的话实例和它的 canvas 会一直挂在内存里，反复进出页面会越来越卡
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="chart" :style="{ height }">
    <div ref="container" class="chart-canvas" />
    <p v-if="!values.length" class="chart-empty">这个区间没有调用记录</p>
  </div>
</template>

<style scoped>
.chart {
  position: relative;
  width: 100%;
}

.chart-canvas {
  width: 100%;
  height: 100%;
}

/* 空数据时 ECharts 画的是一张空白网格，不解释一句用户会以为图挂了 */
.chart-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--faint);
  font-size: var(--fs-sm);
  pointer-events: none;
}
</style>
