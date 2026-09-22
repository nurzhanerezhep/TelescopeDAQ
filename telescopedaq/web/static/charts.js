/* uPlot objects are reused; only the visible chart receives new buffers. */
window.DAQCharts = (() => {
  const palette = [
    "#64d9e4",
    "#79d49b",
    "#e4bc64",
    "#d19df0",
    "#ee878c",
    "#75aaff",
    "#abe271",
    "#e7a777",
  ];
  const plots = new Map();

  function draw(id, series, xLabel, yLabel, options = {}) {
    const el = document.getElementById(id);
    if (!el || el.offsetWidth < 20 || el.closest("[hidden]")) return;
    const dataSeries = series.length
      ? series
      : [
          {
            label: "Waiting for data",
            x: [],
            y: [],
            color: palette[0],
          },
        ];
    // Align channels on sample positions without interpolating across absent samples.
    const xs = [...new Set(dataSeries.flatMap((s) => s.x))].sort(
      (a, b) => a - b,
    );
    const data = [
      xs,
      ...dataSeries.map((s) => {
        const values = new Map(s.x.map((x, i) => [x, s.y[i]]));
        return xs.map((x) => values.get(x) ?? null);
      }),
    ];
    const key = JSON.stringify([
      dataSeries.map((s) => s.label),
      xLabel,
      yLabel,
      options.fixed,
      options.xRange,
    ]);
    let item = plots.get(id);
    if (item && item.key !== key) {
      item.observer.disconnect();
      item.plot.destroy();
      item = null;
    }
    if (!item) {
      const plot = new uPlot(
        {
          width: el.clientWidth,
          height: el.clientHeight,
          pxAlign: 1,
          cursor: {
            drag: {
              x: true,
              y: false,
            },
          },
          legend: {
            show: false,
          },
          scales: {
            x: {
              time: false,
              ...(options.xRange
                ? {
                    range: () => options.xRange,
                  }
                : {}),
            },
            y: options.fixed
              ? {
                  range: () => [0, 4095],
                }
              : {
                  auto: true,
                },
          },
          axes: [
            {
              label: xLabel,
              stroke: "#8fa5ab",
              grid: {
                stroke: "#29373b",
              },
              ticks: {
                stroke: "#35454a",
              },
              size: 42,
              font: "11px Segoe UI",
            },
            {
              label: yLabel,
              stroke: "#8fa5ab",
              grid: {
                stroke: "#29373b",
              },
              ticks: {
                stroke: "#35454a",
              },
              size: 62,
              font: "11px Segoe UI",
            },
          ],
          series: [
            {},
            ...dataSeries.map((s, i) => ({
              label: s.label,
              stroke: s.color || palette[i % palette.length],
              width: 1.3,
              spanGaps: true,
              points: {
                show: false,
              },
            })),
          ],
        },
        data,
        el,
      );
      if (id === "scan-chart")
        plot.over.addEventListener("click", () => {
          const threshold = plot.data[0][plot.cursor.idx];
          if (Number.isFinite(threshold))
            document.getElementById("selected-threshold").value = threshold;
        });
      const observer = new ResizeObserver(() => {
        if (el.clientWidth > 20 && !el.closest("[hidden]"))
          plot.setSize({
            width: el.clientWidth,
            height: el.clientHeight,
          });
      });
      item = {
        plot,
        key,
        observer,
      };
      plots.set(id, item);
      observer.observe(el);
    } else item.plot.setData(data);
  }

  function exportPNG(id) {
    const canvas = document.querySelector(`#${id} canvas`);
    if (!canvas) return;
    const a = document.createElement("a");
    a.download = `${id}.png`;
    a.href = canvas.toDataURL("image/png");
    a.click();
  }
  return {
    draw,
    exportPNG,
    palette,
  };
})();
