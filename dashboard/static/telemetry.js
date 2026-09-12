(() => {
  "use strict";

  const format = (value) => value === null || value === undefined
    ? "N/A"
    : Number(value).toLocaleString("ru-RU");

  function resumeMetric(title) {
    for (const article of document.querySelectorAll("#resume .metric")) {
      if (article.querySelector("h2")?.textContent.trim() === title) return article;
    }
    return null;
  }

  function updateMetric(title, value, note) {
    const card = resumeMetric(title);
    if (!card) return;
    const strong = card.querySelector("strong");
    const paragraph = card.querySelector("p");
    if (strong) strong.textContent = format(value);
    if (paragraph && note) paragraph.textContent = note;
  }

  async function refreshTelemetry() {
    try {
      const response = await fetch("/api/telemetry", {
        cache: "no-store",
        signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) return;
      const data = await response.json();

      const filter = document.querySelector(".flow-node.filter .node-count");
      if (filter) filter.textContent = format(data.hard_filter_rejects);

      const resume = data.resume || {};
      updateMetric(
        "Просмотры",
        resume.views,
        resume.views === null || resume.views === undefined
          ? "Ждём первый snapshot с HH"
          : "Текущее значение на HH"
      );
      updateMetric(
        "Приглашения",
        resume.invitations,
        resume.invitations === null || resume.invitations === undefined
          ? "Ждём первый snapshot с HH"
          : "Текущее значение на HH"
      );
      updateMetric(
        "Подъёмы резюме",
        resume.raises,
        resume.raises_tracking_since
          ? "Накоплено с момента включения телеметрии"
          : "Счётчик начнёт накапливаться после следующего подъёма"
      );
    } catch (_) {
      // Main dashboard keeps the last good snapshot; telemetry does the same.
    }
  }

  refreshTelemetry();
  setInterval(refreshTelemetry, 10000);
})();
