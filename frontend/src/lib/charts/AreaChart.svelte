<script lang="ts">
  import { onMount } from 'svelte';
  import type { Chart as ChartInstance } from 'chart.js';
  import type { SeasonalBaseline, TrendMetrics } from '$lib/types';

  export let points: { date: string; area_m2: number | null; observed: boolean }[] = [];
  export let baseline: SeasonalBaseline | null = null;
  export let trend: TrendMetrics | null = null;

  let canvas: HTMLCanvasElement;
  let chart: ChartInstance | null = null;
  let mounted = false;

  const HA = 10_000;

  function labelFor(date: string): string {
    const [year, month] = date.split('-');
    const names = [
      'Jan',
      'Feb',
      'Mar',
      'Apr',
      'May',
      'Jun',
      'Jul',
      'Aug',
      'Sep',
      'Oct',
      'Nov',
      'Dec'
    ];
    return `${names[Number(month) - 1] ?? month} ${year.slice(2)}`;
  }

  /** Trailing 12-month median: a smooth "normal for this time of year" line. */
  function rollingMedian(values: (number | null)[], window = 12): (number | null)[] {
    const output: (number | null)[] = [];
    for (let index = 0; index < values.length; index += 1) {
      const slice = values
        .slice(Math.max(0, index - window + 1), index + 1)
        .filter((value): value is number => typeof value === 'number');
      if (slice.length === 0) {
        output.push(null);
        continue;
      }
      const sorted = [...slice].sort((a, b) => a - b);
      const mid = Math.floor(sorted.length / 2);
      output.push(
        sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid]
      );
    }
    return output;
  }

  async function render(): Promise<void> {
    if (!mounted || !canvas) {
      return;
    }
    if (!chart) {
      const { default: Chart } = await import('chart.js/auto');
      chart = new Chart(canvas, {
        type: 'line',
        data: { labels: [], datasets: [] },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          animation: { duration: 450 },
          plugins: {
            legend: {
              labels: {
                color: '#94a3b8',
                boxWidth: 10,
                boxHeight: 2,
                font: { size: 10 }
              }
            },
            tooltip: {
              callbacks: {
                label: (context) => {
                  const value = context.parsed.y;
                  return `${context.dataset.label}: ${
                    typeof value === 'number' ? value.toFixed(1) : '—'
                  } ha`;
                }
              }
            }
          },
          scales: {
            x: {
              ticks: { color: '#64748b', maxTicksLimit: 7, font: { size: 10 } },
              grid: { color: 'rgba(148, 163, 184, 0.07)' }
            },
            y: {
              ticks: {
                color: '#64748b',
                font: { size: 10 },
                callback: (value) => `${Number(value).toFixed(0)} ha`
              },
              grid: { color: 'rgba(148, 163, 184, 0.07)' }
            }
          }
        }
      });
    }

    const labels = points.map((point) => labelFor(point.date));
    const areas = points.map((point) =>
      typeof point.area_m2 === 'number' ? point.area_m2 / HA : null
    );
    const threshold = baseline ? baseline.survival_threshold_m2 / HA : null;

    chart.data.labels = labels;
    chart.data.datasets = [
      {
        label: 'Monthly surface area',
        data: areas,
        borderColor: '#22d3ee',
        backgroundColor: 'rgba(34, 211, 238, 0.16)',
        borderWidth: 1.6,
        pointRadius: 0,
        pointHitRadius: 8,
        spanGaps: false,
        fill: true,
        tension: 0.25
      },
      {
        label: '12-month median',
        data: rollingMedian(areas),
        borderColor: '#a5f3fc',
        borderWidth: 1.2,
        borderDash: [4, 3],
        pointRadius: 0,
        spanGaps: true,
        fill: false
      },
      {
        label: 'Natural low (survival threshold)',
        data: labels.map(() => threshold),
        borderColor: '#fb923c',
        borderWidth: 1,
        borderDash: [2, 4],
        pointRadius: 0,
        fill: false
      }
    ];
    chart.update('none');
  }

  onMount(() => {
    mounted = true;
    void render();
    return () => {
      chart?.destroy();
      chart = null;
    };
  });

  $: if (mounted && points && canvas) {
    void render();
  }
</script>

<div class="chart-wrap">
  <canvas bind:this={canvas} aria-label="Lake surface area over time"></canvas>
  {#if trend}
    <p class="chart-note">
      Trend {trend.slope_pct_per_year >= 0 ? '+' : ''}{trend.slope_pct_per_year.toFixed(2)}%/yr ·
      recent 3-year median {trend.permanent_change_pct >= 0 ? '+' : ''}
      {trend.permanent_change_pct.toFixed(1)}%
    </p>
  {/if}
</div>

<style>
  .chart-wrap {
    position: relative;
    height: 230px;
    margin-top: 0.4rem;
  }

  .chart-note {
    position: absolute;
    right: 0;
    bottom: -0.15rem;
    color: #64748b;
    font-size: 0.64rem;
  }
</style>
