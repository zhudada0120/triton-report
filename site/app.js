(function () {
  const REPOS = ['vllm-project/vllm', 'vllm-project/vllm-ascend'];

  let DATA_BASE = '/data';

  async function detectDataBase() {
    // Strategy 1: Try absolute path from root (/data/vllm/index.json).
    // Works under dev server at http://localhost:8000/ or
    // when Pages site is at domain root (e.g. user.github.io).
    try {
      const resp = await fetch(`/data/vllm/index.json`, { method: 'HEAD' });
      if (resp.ok) {
        DATA_BASE = '/data';
        return;
      }
    } catch {}

    // Strategy 2: Detect GitHub Pages sub-path (e.g. /vllm-report/).
    // The page URL tells us the base path; append /data/ to it.
    const pagePath = window.location.pathname;
    const pageDir = pagePath.substring(0, pagePath.lastIndexOf('/') + 1);
    const candidate = pageDir + 'data';
    try {
      const resp = await fetch(`${candidate}/vllm/index.json`, { method: 'HEAD' });
      if (resp.ok) {
        DATA_BASE = candidate;
        return;
      }
    } catch {}

    // Strategy 3: Relative path from page to repo-root data/ directory.
    try {
      const resp = await fetch(`../data/vllm/index.json`, { method: 'HEAD' });
      if (resp.ok) {
        DATA_BASE = '../data';
        return;
      }
    } catch {}
  }

  let currentRepo = REPOS[0];
  let availableDates = [];
  let currentDateIndex = -1;
  let commitsData = null;
  let analysisData = null;
  let activeFilter = 'all';
  let searchQuery = '';
  let analysisDates = [];
  let crossDayResults = null; // { commits: [...], analysis: {...} } from cross-day search
  let sectionsExpanded = { ascend: true, code: false, chore: false, 'needs-test': true, other: false, 'false-positive': true }; // per-section collapse state

  // ── New data shared across enhancements ──
  let adaptationStatus = null;      // sha -> {status, ...} from adaptation-status.json
  let archImpactIndex = null;       // {sha: [interfaces...]} from index.json
  let currentArchTab = 'modules';
  let cachedIndex = null;           // cached index.json content

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  function repoDir(repo) {
    if (repo === 'vllm-project/vllm') return 'vllm';
    if (repo === 'vllm-project/vllm-ascend') return 'vllm-ascend';
    return repo.split('/').pop();
  }

  function dataUrl(repo, type, date) {
    return `${DATA_BASE}/${repoDir(repo)}/${type}/${date}.json`;
  }

  async function fetchJSON(url) {
    try {
      const resp = await fetch(url);
      if (!resp.ok) return null;
      return await resp.json();
    } catch {
      return null;
    }
  }

  async function loadAvailableDates() {
    // Load dates from index.json (which has analysis_dates field)
    const index = await fetchJSON(`${DATA_BASE}/${repoDir(currentRepo)}/index.json`);
    cachedIndex = index;
    if (index && index.analysis_dates && index.analysis_dates.length > 0) {
      availableDates = index.analysis_dates.sort().reverse();
      // Also load architecture impact index
      if (cachedIndex.architecture_impact_index) {
        archImpactIndex = cachedIndex.architecture_impact_index;
      }
      return;
    }

    availableDates = [];
    const meta = await fetchJSON(`${DATA_BASE}/${repoDir(currentRepo)}/meta.json`);
    if (meta && meta.last_fetch_time) {
      const endStr = cnDateStr(new Date(meta.last_fetch_time));
      const todayStr = cnDateStr(new Date());
      const start = new Date(todayStr + 'T00:00:00+08:00');
      const end = new Date(endStr + 'T00:00:00+08:00');
      start.setFullYear(start.getFullYear() - 1);

      const candidates = [];
      const d = new Date(start);
      while (d <= end) {
        candidates.push(cnDateStr(d));
        d.setDate(d.getDate() + 1);
      }
      candidates.sort().reverse();

      for (const date of candidates) {
        const resp = await fetch(dataUrl(currentRepo, 'commits', date), { method: 'HEAD' });
        if (resp.ok) {
          availableDates.push(date);
        }
      }
    }

    if (availableDates.length === 0) {
      availableDates.push(cnDateStr(new Date()));
    }
  }

  async function loadAnalysisDates() {
    // Load analysis dates from cached index.json
    if (cachedIndex && cachedIndex.analysis_dates) {
      analysisDates = cachedIndex.analysis_dates;
      if (cachedIndex.architecture_impact_index) {
        archImpactIndex = cachedIndex.architecture_impact_index;
      }
    } else {
      analysisDates = [];
    }
  }

  function cnDateStr(d) {
    const cnOffset = 8 * 60;
    const utc = d.getTime() + d.getTimezoneOffset() * 60000;
    const cn = new Date(utc + cnOffset * 60000);
    const y = cn.getFullYear();
    const m = String(cn.getMonth() + 1).padStart(2, '0');
    const day = String(cn.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function parseCnTime(isoStr) {
    if (!isoStr) return null;
    if (isoStr.includes('+08:00') || isoStr.includes('T')) {
      const d = new Date(isoStr);
      if (!isNaN(d.getTime())) return d;
    }
    return null;
  }

  function formatTime(isoStr) {
    const d = parseCnTime(isoStr);
    if (!d) {
      if (isoStr && isoStr.length >= 16) return isoStr.slice(11, 16);
      return '--';
    }
    const cn = new Date(d.getTime() + (8 * 60 + d.getTimezoneOffset()) * 60000);
    return String(cn.getHours()).padStart(2, '0') + ':' + String(cn.getMinutes()).padStart(2, '0');
  }

  async function loadDate(date) {
    showLoading(true);
    commitsData = null;
    analysisData = null;
    crossDayResults = null;
    searchQuery = '';
    sectionsExpanded = { ascend: true, code: false, chore: false, 'needs-test': true, other: false };
    $('#searchInput').value = '';
    $('#searchClear').style.display = 'none';

    const [commits, analysis] = await Promise.all([
      fetchJSON(dataUrl(currentRepo, 'commits', date)),
      fetchJSON(dataUrl(currentRepo, 'analysis', date)),
    ]);

    commitsData = commits;
    analysisData = analysis;
    showLoading(false);
    render();
  }

  async function loadCommitsForRange(startStr, endStr) {
    const datesInRange = [];
    const d = new Date(startStr + 'T00:00:00+08:00');
    const end = new Date(endStr + 'T00:00:00+08:00');
    while (d <= end) {
      const ds = cnDateStr(d);
      if (availableDates.includes(ds)) {
        datesInRange.push(ds);
      }
      d.setDate(d.getDate() + 1);
    }

    const [commitResults, analysisResults] = await Promise.all([
      Promise.all(datesInRange.map(function (date) {
        return fetchJSON(dataUrl(currentRepo, 'commits', date));
      })),
      Promise.all(datesInRange.map(function (date) {
        return fetchJSON(dataUrl(currentRepo, 'analysis', date));
      })),
    ]);

    var allCommits = [];
    var allAnalysis = {};

    for (var i = 0; i < commitResults.length; i++) {
      if (commitResults[i] && commitResults[i].commits) {
        commitResults[i].commits.forEach(function (c) {
          c._exportDate = datesInRange[i];
          allCommits.push(c);
        });
      }
    }

    for (var j = 0; j < analysisResults.length; j++) {
      if (analysisResults[j] && analysisResults[j].commits) {
        analysisResults[j].commits.forEach(function (a) {
          allAnalysis[a.sha] = a;
        });
      }
    }

    return { commits: allCommits, analysis: allAnalysis };
  }

  async function exportToExcel() {
    var startDate = $('#rangeStart').value;
    var endDate = $('#rangeEnd').value;

    if (!startDate || !endDate) {
      alert('Please select both start and end dates');
      return;
    }

    if (startDate > endDate) {
      alert('Start date must be before end date');
      return;
    }

    var btn = $('#exportBtn');
    var originalText = btn.textContent;
    btn.textContent = 'Exporting...';
    btn.disabled = true;

    try {
      var result = await loadCommitsForRange(startDate, endDate);

      if (result.commits.length === 0) {
        alert('No commits found in the selected date range');
        btn.textContent = originalText;
        btn.disabled = false;
        return;
      }

      var headers = ['SHA', 'Date', 'Author', 'Title', 'Tags', 'Ascend Affected', 'Needs Test Update', 'AI Analysis', 'Test Impact Reason', 'Changed Files', 'Additions', 'Deletions', 'Files Changed'];
      var rows = [headers];

      result.commits.forEach(function (commit) {
        var a = result.analysis[commit.sha] || null;
        var tags = a && a.tags ? a.tags.join(', ') : '';
        var ascendAffected = a && a.ascend_impact && a.ascend_impact.ascend_affected ? 'Yes' : '';
        var needsTest = a && (
          (a.test_impact && a.test_impact.needs_test_update) ||
          (a.ascend_impact && a.ascend_impact.needs_test_update)
        ) ? 'Yes' : '';
        var comment = a && a.comment ? a.comment : '';
        var testReason = '';
        if (a && a.test_impact && a.test_impact.reason) {
          testReason = a.test_impact.reason;
        } else if (a && a.ascend_impact && a.ascend_impact.needs_test_update) {
          testReason = a.ascend_impact.testing || '';
        }
        var fileList = (commit.files || []).map(function (f) { return f.filename; }).join('; ');
        var title = commit.message.split('\n')[0];
        var additions = commit.stats ? commit.stats.total_additions : 0;
        var deletions = commit.stats ? commit.stats.total_deletions : 0;
        var files = commit.stats ? commit.stats.files_changed : 0;
        var author = (commit.author && commit.author.name) || 'unknown';

        rows.push([
          commit.sha,
          commit._exportDate,
          author,
          title,
          tags,
          ascendAffected,
          needsTest,
          comment,
          testReason,
          fileList,
          additions,
          deletions,
          files
        ]);
      });

      var wb = XLSX.utils.book_new();
      var ws = XLSX.utils.aoa_to_sheet(rows);

      var colWidths = headers.map(function (_, i) {
        var maxLen = headers[i].length;
        rows.forEach(function (row) {
          var len = String(row[i] || '').length;
          if (len > maxLen) maxLen = len;
        });
        return { wch: Math.min(maxLen + 3, 80) };
      });
      ws['!cols'] = colWidths;

      XLSX.utils.book_append_sheet(wb, ws, 'Commits');

      var wbout = XLSX.write(wb, { bookType: 'xlsx', type: 'array' });
      var blob = new Blob([wbout], { type: 'application/octet-stream' });
      var link = document.createElement('a');
      var prefix = repoDir(currentRepo);
      link.href = URL.createObjectURL(blob);
      link.download = prefix + '-commits-' + startDate + '-' + endDate + '.xlsx';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(link.href);
    } catch (err) {
      alert('Export failed: ' + (err.message || 'unknown error'));
    } finally {
      btn.textContent = originalText;
      btn.disabled = false;
    }
  }

  function showLoading(show) {
    $('#loading').style.display = show ? 'flex' : 'none';
    if (show) {
      $('#commitList').innerHTML = '';
      $('#emptyState').style.display = 'none';
    }
  }

  function getAnalysisForSha(sha) {
    if (!analysisData || !analysisData.commits) return null;
    return analysisData.commits.find((c) => c.sha === sha);
  }

  // vllm: 'ascend' | 'code' | 'chore'
  // vllm-ascend: 'needs-test' | 'chore'
  function classifyCommit(commit) {
    var a = getAnalysisForSha(commit.sha);
    if (currentRepo === 'vllm-project/vllm-ascend') {
      if (a && a.test_impact && a.test_impact.needs_test_update) return 'needs-test';
      return 'chore';
    }
    // vllm repo
    // 有 deep_analysis 但被确认为 false positive 的单独归类
    if (a && a.deep_analysis && a.deep_analysis.ascend_affected_confirmed === false) return 'false-positive';
    if (a && a.ascend_impact && a.ascend_impact.ascend_affected === true) return 'ascend';
    // Check if auto-triaged (comment starts with "（自动判定）")
    if (a && a.comment && a.comment.indexOf('（自动判定）') === 0) return 'chore';
    if (a && a.ascend_impact && a.ascend_impact.ascend_affected === false) return 'code';
    // No analysis at all — treat as code (needs attention)
    return 'code';
  }

  function classifyCrossDay(commit, analysisMap) {
    var a = analysisMap ? analysisMap[commit.sha] : null;
    if (currentRepo === 'vllm-project/vllm-ascend') {
      if (a && a.test_impact && a.test_impact.needs_test_update) return 'needs-test';
      return 'chore';
    }
    if (a && a.deep_analysis && a.deep_analysis.ascend_affected_confirmed === false) return 'false-positive';
    if (a && a.ascend_impact && a.ascend_impact.ascend_affected === true) return 'ascend';
    if (a && a.comment && a.comment.indexOf('（自动判定）') === 0) return 'chore';
    if (a && a.ascend_impact && a.ascend_impact.ascend_affected === false) return 'code';
    return 'code';
  }

  function filterCommits(commits, analysisMap) {
    if (!commits) return [];
    return commits.filter((c) => {
      var a = analysisMap ? analysisMap[c.sha] : getAnalysisForSha(c.sha);
      const q = searchQuery.toLowerCase();
      const matchesSearch =
        !q ||
        c.message.toLowerCase().includes(q) ||
        c.sha.toLowerCase().includes(q) ||
        (c.author && c.author.name.toLowerCase().includes(q)) ||
        (a && a.comment && a.comment.toLowerCase().includes(q)) ||
        (c.files && c.files.some(f => f.filename.toLowerCase().includes(q)));

      if (!matchesSearch) return false;

      if (activeFilter === 'all') return true;
      if (!a) return false;

      // Adaptation status filters (vllm-ascend only)
      if (activeFilter === 'adapt-pending' || activeFilter === 'adapt-adapted') {
        if (!adaptationStatus) return false;
        var adapt = adaptationStatus[c.sha];
        if (!adapt) return false;
        if (activeFilter === 'adapt-pending') return adapt.status === 'pending';
        if (activeFilter === 'adapt-adapted') return adapt.status === 'adapted';
        return false;
      }

      switch (activeFilter) {
        case 'needs-test':
          return (a.test_impact && a.test_impact.needs_test_update) || (a.ascend_impact && a.ascend_impact.needs_test_update);
        case 'affects-ascend':
          return a.ascend_impact && a.ascend_impact.ascend_affected === true;
        case 'high-risk':
          return a.tags && a.tags.some((t) => t === 'high-risk');
        default:
          return a.tags && a.tags.includes(activeFilter);
      }
    });
  }

  function renderStats(commits) {
    let totalAdd = 0, totalDel = 0, totalFiles = 0;
    (commits || []).forEach((c) => {
      if (c.stats) {
        totalAdd += c.stats.total_additions || 0;
        totalDel += c.stats.total_deletions || 0;
        totalFiles += c.stats.files_changed || 0;
      }
    });
    $('#statCommits').textContent = commits ? commits.length : 0;
    $('#statAdditions').textContent = '+' + totalAdd.toLocaleString();
    $('#statDeletions').textContent = '-' + totalDel.toLocaleString();
    $('#statFiles').textContent = totalFiles;
  }

  function updateFilterChips(allCommits) {
    var filters = ['all', 'needs-test', 'affects-ascend', 'high-risk', 'feature', 'bugfix', 'refactor', 'performance'];
    // Add adaptation status filters (only for vllm repo, where upstream commits are tracked)
    if (currentRepo === 'vllm-project/vllm' && adaptationStatus) {
      filters.push('adapt-pending', 'adapt-adapted');
    }
    var saved = activeFilter;
    var counts = {};
    for (var i = 0; i < filters.length; i++) {
      activeFilter = filters[i];
      counts[filters[i]] = filterCommits(allCommits).length;
    }
    activeFilter = saved;
    $$('.filter-chip').forEach(function (chip) {
      var f = chip.dataset.filter;
      var count = counts[f] || 0;
      var label = chip.textContent.replace(/\s*\(\d+\)$/, '');
      chip.textContent = label + ' (' + count + ')';
    });
  }

  function renderSummary() {
    const el = $('#dailySummary');
    if (analysisData && analysisData.daily_summary) {
      el.style.display = 'block';
      $('#summaryText').innerHTML = renderMarkdown(analysisData.daily_summary);
    } else {
      el.style.display = 'none';
    }
  }

  function tagClass(tag) {
    if (tag === 'high-risk') return 'tag risk-high';
    if (tag === 'medium-risk') return 'tag risk-medium';
    if (tag === 'low-risk') return 'tag risk-low';
    if (['feature', 'bugfix', 'refactor', 'performance'].includes(tag)) return `tag type-${tag}`;
    return 'tag';
  }

  function renderDiff(patch) {
    if (!patch) return '';
    const lines = patch.split('\n');
    let html = '';
    for (const line of lines) {
      let cls = 'line-ctx';
      let content = escapeHtml(line);
      if (line.startsWith('@@')) cls = 'line-hunk';
      else if (line.startsWith('+')) cls = 'line-add';
      else if (line.startsWith('-')) cls = 'line-del';
      html += `<div class="${cls}">${content}</div>`;
    }
    return html;
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function highlightText(text, query) {
    if (!query) return escapeHtml(text);
    var escaped = escapeHtml(text);
    var q = escapeHtml(query).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var re = new RegExp('(' + q + ')', 'gi');
    return escaped.replace(re, '<mark>$1</mark>');
  }

  function renderMarkdown(str) {
    if (!str) return '';
    var s = escapeHtml(str);
    var blocks = [];
    s = s.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
      var placeholder = '%%CODEBLOCK' + blocks.length + '%%';
      blocks.push('<pre><code>' + code.trim() + '</code></pre>');
      return placeholder;
    });
    // Inline code `...`
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold **text** or __text__
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__(.+?)__/g, '<strong>$1</strong>');
    // Italic *text* or _text_ (single, but not inside words)
    s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
    s = s.replace(/(?<![:\w])_([^_\n]+)_(?![:\w])/g, '<em>$1</em>');
    // Links [text](url) — sanitize to http/https only
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, text, url) {
      url = url.trim();
      if (!url.startsWith('http://') && !url.startsWith('https://')) return text;
      return '<a href="' + escapeHtml(url) + '" target="_blank">' + text + '</a>';
    });
    // ### headings
    s = s.replace(/^### (.+)$/gm, '<h4>$1</h4>');
    s = s.replace(/^## (.+)$/gm, '<h3>$1</h3>');
    s = s.replace(/^# (.+)$/gm, '<h2>$1</h2>');
    // Unordered list items - wrap consecutive - items in <ul>
    s = s.replace(/^- (.+)$/gm, '<li>$1</li>');
    s = s.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
    // Ordered list items
    s = s.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    s = s.replace(/(?:<li>.*<\/li>\n?)+/g, function (match) {
      if (match.indexOf('<ul>') === -1) {
        return '<ol>' + match + '</ol>';
      }
      return match;
    });
    // Protect list and code block contents from line break conversion
    var listBlocks = [];
    // Compress blank lines between list items before protection
    s = s.replace(/(<li>[^<]*<\/li>)\n\n+/g, '$1\n');
    s = s.replace(/(<[ou]l>[\s\S]*?<\/[ou]l>)/g, function (m) {
      var p = '%%LISTBLOCK' + listBlocks.length + '%%';
      listBlocks.push(m);
      return p;
    });
    // Double line breaks = new paragraph
    s = s.replace(/\n\n/g, '</p><p>');
    // Single line break
    s = s.replace(/\n/g, '<br>');
    // Restore list blocks
    for (var li = 0; li < listBlocks.length; li++) {
      s = s.replace('%%LISTBLOCK' + li + '%%', listBlocks[li]);
    }
    // Restore code blocks
    for (var bi = 0; bi < blocks.length; bi++) {
      s = s.replace('%%CODEBLOCK' + bi + '%%', blocks[bi]);
    }
    // Wrap in paragraph if not already wrapped
    if (!s.startsWith('<')) {
      s = '<p>' + s + '</p>';
    }
    return s;
  }

  // ── Adaptation Status (P1) ────────────────────
  async function loadAdaptationStatus() {
    const data = await fetchJSON(`${DATA_BASE}/vllm-ascend/adaptation-status.json`);
    if (!data || !data.commits) {
      adaptationStatus = null;
      return;
    }
    const statusMap = {};
    data.commits.forEach(function (c) {
      // Use upstream_sha if available, fallback to sha
      var key = c.upstream_sha || c.sha;
      statusMap[key] = c;
    });
    adaptationStatus = statusMap;
  }

  function getAdaptBadgeHtml(sha) {
    if (!adaptationStatus) return '';
    var adapt = adaptationStatus[sha];
    if (!adapt) return '';
    var status = adapt.status || 'pending';
    if (status === 'pending') return '';
    var labels = {
      adapted: 'Adapted'
    };
    var label = labels[status] || status;
    return `<span class="adapt-badge ${status}" data-adapt-sha="${sha}">${label}</span>`;
  }

  // ── Architecture Impact Marker (P3) ───────────
  function getArchImpactHtml(sha) {
    if (!archImpactIndex) return '';
    var impact = archImpactIndex[sha];
    if (!impact) return '';
    var interfaces = impact.affected_interfaces;
    if (Array.isArray(interfaces) && interfaces.length > 0) {
      var text = interfaces.join(', ');
      return `<span class="arch-impact-marker" title="Affected interfaces: ${escapeHtml(text)}">⚡ Arch Impact: ${escapeHtml(text.substring(0, 60))}</span>`;
    }
    return '';
  }

  // ── Baseline Info (P3) ────────────────────────
  async function loadBaseline() {
    if (currentRepo !== 'vllm-project/vllm-ascend') {
      $('#baselineBar').style.display = 'none';
      return;
    }
    try {
      // Read main baseline SHA
      const mainResp = await fetch(
        'https://api.github.com/repos/vllm-project/vllm-ascend/contents/.github/vllm-main-verified.commit'
      );
      // Read release tag
      const releaseResp = await fetch(
        'https://api.github.com/repos/vllm-project/vllm-ascend/contents/.github/vllm-release-tag.commit'
      );
      if (!mainResp.ok || !releaseResp.ok) {
        // Fallback: read from adaptation-status.json stats
        showBaselineFromStats();
        return;
      }
      const mainData = await mainResp.json();
      const releaseData = await releaseResp.json();
      var mainSha = atob(mainData.content).trim();
      var releaseTag = atob(releaseData.content).trim();

      // Get stats from adaptation-status.json
      var adaptData = await fetchJSON(`${DATA_BASE}/vllm-ascend/adaptation-status.json`);
      var stats = adaptData && adaptData.stats ? adaptData.stats : {};

      var html = '';
      html += '<span class="baseline-text">';
      html += 'Main: <code>' + mainSha.substring(0, 12) + '</code>';
      if (releaseTag) html += ' &nbsp;|&nbsp; Release: <code>' + escapeHtml(releaseTag) + '</code>';
      if (stats.total) {
        html += ' &nbsp;|&nbsp; ';
        html += '<span class="baseline-status pending">Pending: ' + (stats.pending || 0) + '</span> ';
        html += '<span class="baseline-status adapted">Adapted: ' + (stats.adapted || 0) + '</span> ';
      }
      html += '</span>';

      $('#baselineText').innerHTML = html;
      $('#baselineBar').style.display = 'block';
    } catch {
      showBaselineFromStats();
    }
  }

  async function showBaselineFromStats() {
    try {
      var adaptData = await fetchJSON(`${DATA_BASE}/vllm-ascend/adaptation-status.json`);
      var stats = adaptData && adaptData.stats ? adaptData.stats : {};
      if (!stats.total) {
        $('#baselineBar').style.display = 'none';
        return;
      }
      var html = '';
      html += '<span class="baseline-text">Adaptation: ';
      html += '<span class="baseline-status pending">Pending: ' + (stats.pending || 0) + '</span> ';
      html += '<span class="baseline-status adapted">Adapted: ' + (stats.adapted || 0) + '</span> ';
      html += '</span>';
      $('#baselineText').innerHTML = html;
      $('#baselineBar').style.display = 'block';
    } catch {
      $('#baselineBar').style.display = 'none';
    }
  }

  // ── Search Acceleration (P2) ──────────────────
  async function searchAcrossDates() {
    if (!searchQuery) return;
    showLoading(true);

    // Try to use cached index.json for fast pre-filtering
    var index = cachedIndex || await fetchJSON(`${DATA_BASE}/${repoDir(currentRepo)}/index.json`);
    var matchedDates = null;

    if (index && index.keyword_index) {
      // Use keyword_index to find matching SHAs first
      var kw = searchQuery.toLowerCase();
      var kwIndex = index.keyword_index;
      var matchingShas = new Set();

      // Check if keyword directly matches any index entry
      for (var key in kwIndex) {
        if (key.indexOf(kw) !== -1 || kw.indexOf(key) !== -1) {
          kwIndex[key].forEach(function (sha) { matchingShas.add(sha); });
        }
      }

      // If no keyword match, try tags_index
      if (matchingShas.size === 0 && index.tags_index) {
        var tagsIndex = index.tags_index;
        for (var tag in tagsIndex) {
          if (tag.indexOf(kw) !== -1 || kw.indexOf(tag) !== -1) {
            tagsIndex[tag].forEach(function (sha) { matchingShas.add(sha); });
          }
        }
      }

      // Resolve SHAs to dates using commits-index
      if (matchingShas.size > 0) {
        var commitsIndex = await fetchJSON(`${DATA_BASE}/${repoDir(currentRepo)}/commits-index.json`);
        if (commitsIndex) {
          var dateSet = new Set();
          matchingShas.forEach(function (sha) {
            var info = commitsIndex[sha];
            if (info && info.date) dateSet.add(info.date);
          });
          matchedDates = Array.from(dateSet).sort().reverse();
        }
      }
    }

    // If index didn't help, fall back to scanning all dates
    if (!matchedDates) {
      matchedDates = availableDates.slice();
    }

    var allCommits = [];
    var allAnalysis = {};

    // Fetch in parallel batches of 10
    var batchSize = 10;
    for (var i = 0; i < matchedDates.length; i += batchSize) {
      var batch = matchedDates.slice(i, i + batchSize);
      var results = await Promise.all(batch.map(function (date) {
        return Promise.all([
          fetchJSON(dataUrl(currentRepo, 'commits', date)),
          fetchJSON(dataUrl(currentRepo, 'analysis', date)),
        ]);
      }));
      for (var j = 0; j < results.length; j++) {
        var commitsData2 = results[j][0];
        var analysisData2 = results[j][1];
        if (commitsData2 && commitsData2.commits) {
          for (var k = 0; k < commitsData2.commits.length; k++) {
            commitsData2.commits[k]._date = batch[j];
            allCommits.push(commitsData2.commits[k]);
          }
        }
        if (analysisData2 && analysisData2.commits) {
          for (var m = 0; m < analysisData2.commits.length; m++) {
            allAnalysis[analysisData2.commits[m].sha] = analysisData2.commits[m];
          }
        }
      }
    }

    var filtered = filterCommits(allCommits, allAnalysis);
    crossDayResults = { commits: filtered, analysis: allAnalysis };
    sectionsExpanded = { ascend: true, code: true, chore: true, 'needs-test': true, other: true, 'false-positive': true }; // cross-day search expands all
    showLoading(false);
    render();
  }

  // ── Render ─────────────────────────────────────
  function renderCommitCard(commit) {
    const a = getAnalysisForSha(commit.sha);
    const hasAnalysis = !!a;
    const isHighRisk = a && a.tags && a.tags.includes('high-risk');

    const title = commit.message.split('\n')[0];
    const body = commit.message.split('\n').slice(1).join('\n').trim();
    const shaShort = commit.sha.slice(0, 8);
    const additions = commit.stats ? commit.stats.total_additions : 0;
    const deletions = commit.stats ? commit.stats.total_deletions : 0;
    const files = commit.stats ? commit.stats.files_changed : 0;

    let cardClass = 'commit-card';
    if (hasAnalysis) cardClass += ' has-analysis';
    if (isHighRisk) cardClass += ' high-risk';

    let html = `<div class="${cardClass}" data-sha="${commit.sha}">`;
    html += `<div class="commit-header">`;
    html += `<span class="expand-arrow">▶</span>`;
    html += `<a class="commit-sha" href="https://github.com/${currentRepo}/commit/${commit.sha}" target="_blank">${shaShort}</a>`;
    html += `<div class="commit-message">`;
    html += `<div class="commit-title">${highlightText(title, searchQuery)}</div>`;
    if (body) html += `<div class="commit-body">${escapeHtml(body)}</div>`;
    html += `</div>`;

    if (hasAnalysis && a.tags) {
      html += `<div class="tag-list">`;
      for (const tag of a.tags) {
        html += `<span class="${tagClass(tag)}">${escapeHtml(tag)}</span>`;
      }
      html += `</div>`;
    }

    // ── Enhancement: Adaptation Status Badge (P1) ──
    var adaptBadge = getAdaptBadgeHtml(commit.sha);
    if (adaptBadge) {
      html += `<div style="display:flex;align-items:center;gap:6px;flex-shrink:0;padding:0 16px 2px">${adaptBadge}</div>`;
    }

    // ── Enhancement: Architecture Impact Marker (P3) ──
    var archImpact = getArchImpactHtml(commit.sha);
    if (archImpact) {
      html += `<div style="display:flex;align-items:center;gap:6px;flex-shrink:0;padding:0 16px 2px">${archImpact}</div>`;
    }

    html += `<div class="commit-meta">`;
    html += `<span class="commit-author">${escapeHtml((commit.author && commit.author.name) || 'unknown')}</span>`;
    html += `<span class="commit-time">${formatTime(commit.date)}</span>`;
    html += `<span class="stat-badge additions">+${additions}</span>`;
    html += `<span class="stat-badge deletions">-${deletions}</span>`;
    html += `<span class="stat-badge files">${files}f</span>`;
    html += `</div>`;
    html += `</div>`;

    if (hasAnalysis) {
      html += `<div class="analysis-section">`;
      if (a.comment || a.content) {
        html += `<div class="ai-comment"><div class="ai-label">AI Analysis</div>${renderMarkdown(a.comment || a.content)}</div>`;
      }
      if (a.test_impact) {
        html += `<div class="impact-card test-impact">`;
        html += `<div class="impact-label${a.test_impact.needs_test_update ? ' needs-test' : ''}">${a.test_impact.needs_test_update ? '[!] Test Update Needed' : 'Test Impact'}</div>`;
        html += `<div class="impact-text"><strong>Reason:</strong> ${renderMarkdown(a.test_impact.reason || '')}</div>`;
        if (a.test_impact.suggested_test_areas && a.test_impact.suggested_test_areas.length > 0) {
          html += `<div class="impact-text" style="margin-top:4px"><strong>Areas:</strong> ${a.test_impact.suggested_test_areas.map(escapeHtml).join(', ')}</div>`;
        }
        html += `</div>`;
      }
      if (a.ascend_impact) {
        const funcImp = a.ascend_impact.functionality || '';
        const testImp = a.ascend_impact.testing || '';
        html += `<div class="impact-card ascend-impact">`;
        html += `<div class="impact-label ascend">Ascend Impact</div>`;
        if (funcImp) {
          html += `<div class="impact-text"><strong>Functionality:</strong> ${renderMarkdown(funcImp)}</div>`;
        }
        if (testImp) {
          html += `<div class="impact-text" style="margin-top:4px"><strong>Testing:</strong> ${renderMarkdown(testImp)}</div>`;
        }
        if (a.ascend_impact.needs_test_update) {
          html += `<div class="impact-text" style="margin-top:4px"><span class="stat-badge additions" style="font-size:0.75rem">[!] Test Update Needed</span></div>`;
          if (a.ascend_impact.suggested_test_areas && a.ascend_impact.suggested_test_areas.length > 0) {
            html += `<div class="impact-text" style="margin-top:4px"><strong>Areas:</strong> ${a.ascend_impact.suggested_test_areas.map(escapeHtml).join(', ')}</div>`;
          }
        }
        html += `</div>`;
      }
      if (a.deep_analysis) {
        const da = a.deep_analysis;
        if (da.ascend_affected_confirmed === false) {
          html += `<div class="impact-card deep-analysis">`;
          html += `<div class="impact-label" style="color:#e67e22">Phase 2: No Adaptation Needed</div>`;
          html += `<div class="impact-text">AI 深度分析确认此 commit 无需适配 vllm-ascend</div>`;
          if (da.adaptation_guide) {
            html += `<div class="impact-text" style="margin-top:4px"><strong>分析详情:</strong> ${renderMarkdown(da.adaptation_guide)}</div>`;
          }
          html += `</div>`;
        } else {
          html += `<div class="impact-card deep-analysis">`;
          html += `<div class="impact-label deep-analysis">Deep Analysis</div>`;
          if (da.ascend_affected_confirmed === true) {
            html += `<div class="impact-text" style="margin-top:4px"><strong>Confirmed:</strong> 需要适配</div>`;
          }
          if (da.affected_interfaces && da.affected_interfaces.length > 0) {
            html += `<div class="impact-text"><strong>Affected Interfaces:</strong> ${da.affected_interfaces.map(escapeHtml).join(', ')}</div>`;
          }
          if (da.adaptation_effort) {
            html += `<div class="impact-text" style="margin-top:4px"><strong>Effort:</strong> ${escapeHtml(da.adaptation_effort)}</div>`;
          }
          if (da.adaptation_guide) {
            html += `<div class="impact-text" style="margin-top:4px"><strong>Guide:</strong> ${renderMarkdown(da.adaptation_guide)}</div>`;
          }
          if (da.risk) {
            html += `<div class="impact-text" style="margin-top:4px"><strong>Risk:</strong> ${escapeHtml(da.risk)}</div>`;
          }
          html += `</div>`;
        }
      }
      html += `</div>`;
    }

    if (commit.files && commit.files.length > 0) {
      html += `<div class="diff-section">`;
      html += `<div class="diff-toggle" data-sha="${commit.sha}"><span class="arrow">▶</span> ${commit.files.length} file(s) changed</div>`;
      html += `<div class="diff-content" id="diff-${commit.sha}">`;
      for (const file of commit.files) {
        html += `<div class="file-diff">`;
        html += `<div class="file-diff-header">`;
        html += `<span class="filename">${escapeHtml(file.filename)}</span>`;
        html += `<div class="file-stats"><span class="add">+${file.additions}</span><span class="del">-${file.deletions}</span></div>`;
        html += `</div>`;
        html += `<div class="file-diff-body"><pre>${renderDiff(file.patch)}</pre></div>`;
        html += `</div>`;
      }
      html += `</div></div>`;
    }

    html += `</div>`;
    return html;
  }

  var currentMonth = null;

  function renderDateBar() {
    var targetDate;
    if (currentMonth) {
      targetDate = cnDateStr(currentMonth);
    } else if (availableDates.length > 0 && currentDateIndex < availableDates.length) {
      targetDate = availableDates[currentDateIndex];
    } else {
      targetDate = cnDateStr(new Date());
    }
    var ref = new Date(targetDate + 'T00:00:00+08:00');
    var year = ref.getFullYear();
    var mon = ref.getMonth(); // 0-indexed

    var firstDay = new Date(year, mon, 1);
    var lastDay = new Date(year, mon + 1, 0);
    var startDow = firstDay.getDay(); // 0=Sun

    var monthNames = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    var weekdayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var currentDate = availableDates[currentDateIndex];
    var todayStr = cnDateStr(new Date());
    var html = '';

    // Header: prev + month label + next
    var prevMonth = mon - 1;
    var prevYear = year;
    if (prevMonth < 0) { prevMonth = 11; prevYear--; }
    var nextMonth = mon + 1;
    var nextYear = year;
    if (nextMonth > 11) { nextMonth = 0; nextYear++; }
    var nextDisabled = nextYear > new Date().getFullYear() || (nextYear === new Date().getFullYear() && nextMonth > new Date().getMonth());

    html += '<div class="calendar-header">';
    html += '<button class="date-nav" id="datePrev" data-offset="' + prevYear + '-' + String(prevMonth + 1).padStart(2, '0') + '">◀</button>';
    html += '<span class="month-label">' + monthNames[mon] + ' ' + year + '</span>';
    html += '<button class="date-nav" id="dateNext" data-offset="' + nextYear + '-' + String(nextMonth + 1).padStart(2, '0') + '"' + (nextDisabled ? ' disabled' : '') + '>▶</button>';
    html += '</div>';

    // Weekday headers
    html += '<div class="calendar-grid">';
    for (var w = 0; w < 7; w++) {
      html += '<div class="calendar-weekday">' + weekdayNames[w] + '</div>';
    }

    // Fill leading empty cells
    for (var i = 0; i < startDow; i++) {
      html += '<div class="calendar-cell empty"></div>';
    }

    // Days of the month
    var d = new Date(firstDay);
    while (d <= lastDay) {
      var ds = cnDateStr(d);
      var dayNum = d.getDate();
      var hasData = availableDates.indexOf(ds) !== -1;
      var hasAnalysis = analysisDates.indexOf(ds) !== -1;
      var isActive = ds === currentDate;
      var isToday = ds === todayStr;
      var cls = 'calendar-cell';
      if (hasData) cls += ' has-data';
      if (hasAnalysis && hasData) cls += ' has-analysis';
      if (isActive) cls += ' active';
      if (isToday) cls += ' today';
      html += '<div class="' + cls + '" data-date="' + ds + '">' + dayNum + '</div>';
      d.setDate(d.getDate() + 1);
    }

    html += '</div>'; // .calendar-grid

    $('#dateBar').innerHTML = html;
  }

  function restoreExpanded() {
    var saved = sessionStorage.getItem('vllmExpanded');
    if (!saved) return;
    var shas = JSON.parse(saved);
    if (!Array.isArray(shas)) return;
    shas.forEach(function (sha) {
      var card = document.querySelector('.commit-card[data-sha="' + sha + '"]');
      if (card) card.classList.add('expanded');
    });
  }

  function renderCoverageBar() {
    var el = $('#sideCoverageBar');
    if (!availableDates.length || !analysisDates.length) {
      el.innerHTML = '<span class="coverage-text">No data</span>';
      return;
    }
    var total = availableDates.length;
    var analyzed = 0;
    for (var i = 0; i < availableDates.length; i++) {
      if (analysisDates.indexOf(availableDates[i]) !== -1) {
        analyzed++;
      }
    }
    var pct = Math.round(analyzed / total * 100);
    var missing = total - analyzed;
    var color = missing === 0 ? 'var(--accent)' : (missing < 5 ? 'var(--accent-orange)' : 'var(--accent-red)');
    el.innerHTML = '<span class="coverage-label">Analysis Coverage</span>' +
      '<div class="coverage-track"><div class="coverage-fill" style="width:' + pct + '%;background:' + color + '"></div></div>' +
      '<span class="coverage-text">' + analyzed + '/' + total + ' days</span>';
  }

  function computeModuleHeatmap(commits) {
    if (!commits || !commits.length) return [];
    var counts = {};
    commits.forEach(function (c) {
      if (!c.files) return;
      var seen = {};
      c.files.forEach(function (f) {
        var parts = f.filename.split('/');
        var key;
        if (parts.length >= 3) {
          key = parts[0] + '/' + parts[1] + '/' + parts[2] + '/';
        } else if (parts.length >= 2) {
          key = parts[0] + '/' + parts[1] + '/';
        } else {
          key = parts[0];
        }
        if (!seen[key]) {
          seen[key] = true;
          counts[key] = (counts[key] || 0) + 1;
        }
      });
    });
    var sorted = Object.keys(counts).map(function (k) { return { path: k, count: counts[k] }; });
    sorted.sort(function (a, b) { return b.count - a.count; });
    return sorted.slice(0, 10);
  }

  function renderHeatmap(commits) {
    var el = $('#heatmapSection');
    var bar = $('#heatmapBar');
    var modules = computeModuleHeatmap(commits);
    if (!modules.length) {
      el.style.display = 'none';
      return;
    }
    el.style.display = 'block';
    var maxCount = modules[0].count;
    var html = '';
    for (var i = 0; i < modules.length; i++) {
      var m = modules[i];
      var w = Math.round(m.count / maxCount * 100);
      html += '<div class="heatmap-row"><span class="heatmap-path">' + escapeHtml(m.path) + '</span>' +
        '<div class="heatmap-track"><div class="heatmap-fill" style="width:' + w + '%"></div></div>' +
        '<span class="heatmap-count">' + m.count + '</span></div>';
    }
    bar.innerHTML = html;
  }

  function renderSidebar(commits) {
    renderCoverageBar();
    renderHeatmap(commits);
  }

  function render() {
    if (!commitsData || !commitsData.commits) {
      $('#emptyState').style.display = 'block';
      $('#emptyState').querySelector('.title').textContent = 'No data available';
      $('#emptyState').querySelector('.subtitle').textContent = 'Try selecting a different date or repository';
      $('#commitList').innerHTML = '';
      $('#heatmapSection').style.display = 'none';
      renderStats([]);
      renderSummary();
      renderDateBar();
      return;
    }

    if (commitsData.commits.length === 0) {
      $('#emptyState').style.display = 'block';
      $('#emptyState').querySelector('.title').textContent = '当日无提交';
      $('#emptyState').querySelector('.subtitle').textContent = currentRepo + ' 在 ' + (commitsData.date || availableDates[currentDateIndex]) + ' 没有新的 commit 记录';
      $('#commitList').innerHTML = '';
      $('#heatmapSection').style.display = 'none';
      renderStats([]);
      renderSummary();
      renderDateBar();
      return;
    }

    if (crossDayResults) {
      renderGroupedCommitList(crossDayResults.commits, crossDayResults.analysis);
      return;
    }

    $('#emptyState').style.display = 'none';
    renderSummary();
    renderStats(commitsData.commits);
    renderDateBar();
    renderSidebar(commitsData.commits);

    updateFilterChips(commitsData.commits);
    renderGroupedCommitList(commitsData.commits);
  }

  function renderGroupedCommitList(commits, analysisMap) {
    // Classify all commits
    var groups = {};
    var allFiltered = filterCommits(commits, analysisMap);

    if (allFiltered.length === 0) {
      if (searchQuery) {
        $('#commitList').innerHTML = '<div class="empty-state"><div class="title">No matching commits today</div><div class="subtitle">Try adjusting your filter or <button class="cross-search-btn" id="crossSearchBtn">Search across all dates</button></div></div>';
      } else {
        $('#commitList').innerHTML = '<div class="empty-state"><div class="title">No matching commits</div><div class="subtitle">Try adjusting your filter or search</div></div>';
      }
      return;
    }

    // Group by classification
    for (var i = 0; i < allFiltered.length; i++) {
      var c = allFiltered[i];
      var cls = analysisMap ? classifyCrossDay(c, analysisMap) : classifyCommit(c);
      if (!groups[cls]) groups[cls] = [];
      groups[cls].push(c);
    }

    var isVllm = currentRepo === 'vllm-project/vllm';
    var primaryKey = isVllm ? 'ascend' : 'needs-test';
    var primaryCommits = groups[primaryKey] || [];
    var fpCommits = groups['false-positive'] || [];
    var codeCommits = isVllm ? (groups['code'] || []) : [];
    var choreCommits = groups['chore'] || [];
    var otherCount = codeCommits.length + choreCommits.length;

    // Build HTML
    var html = '';

    // Primary section (collapsible)
    if (primaryCommits.length > 0) {
      var primaryLabel = isVllm ? 'Ascend Impact' : 'Needs Test Update';
      var primaryExpanded = sectionsExpanded[primaryKey];
      html += '<div class="commit-section">';
      html += '<div class="section-header section-primary" data-section="' + primaryKey + '">';
      html += '<span class="section-arrow">' + (primaryExpanded ? '▼' : '▶') + '</span> ';
      html += primaryLabel + ' (' + primaryCommits.length + ')';
      html += '</div>';
      html += '<div class="section-body" style="' + (primaryExpanded ? '' : 'display:none') + '">';
      for (var j = 0; j < primaryCommits.length; j++) {
        html += renderCommitCardForCommit(primaryCommits[j], analysisMap);
      }
      html += '</div></div>';
    }

    // False Positive section (deep_analyzed but no adaptation needed)
    if (fpCommits.length > 0) {
      var fpExpanded = sectionsExpanded['false-positive'];
      html += '<div class="commit-section">';
      html += '<div class="section-header section-false-positive" data-section="false-positive">';
      html += '<span class="section-arrow">' + (fpExpanded ? '▼' : '▶') + '</span> ';
      html += 'False Positive (Deep Analyzed, No Adaptation) (' + fpCommits.length + ')';
      html += '</div>';
      html += '<div class="section-body" style="' + (fpExpanded ? '' : 'display:none') + '">';
      for (var n = 0; n < fpCommits.length; n++) {
        html += renderCommitCardForCommit(fpCommits[n], analysisMap);
      }
      html += '</div></div>';
    }

    // Other Changes section (collapsible, contains sub-sections)
    if (otherCount > 0) {
      var otherExpanded = sectionsExpanded['other'];
      html += '<div class="commit-section">';
      html += '<div class="section-header section-other" data-section="other">';
      html += '<span class="section-arrow">' + (otherExpanded ? '▼' : '▶') + '</span> ';
      html += 'Other Changes (' + otherCount + ')';
      html += '</div>';
      html += '<div class="section-body" style="' + (otherExpanded ? '' : 'display:none') + '">';

      // Sub-section: Code Changes (vllm only, collapsible)
      if (isVllm && codeCommits.length > 0) {
        var codeExpanded = sectionsExpanded['code'];
        html += '<div class="subsection-block">';
        html += '<div class="subsection-header collapsible" data-section="code">';
        html += '<span class="section-arrow">' + (codeExpanded ? '▼' : '▶') + '</span> ';
        html += 'Code Changes (' + codeCommits.length + ')';
        html += '</div>';
        html += '<div class="section-body" style="' + (codeExpanded ? '' : 'display:none') + '">';
        for (var k = 0; k < codeCommits.length; k++) {
          html += renderCommitCardForCommit(codeCommits[k], analysisMap);
        }
        html += '</div></div>';
      }

      // Sub-section: Chores (collapsible)
      if (choreCommits.length > 0) {
        var choreExpanded = sectionsExpanded['chore'];
        var choreLabel = isVllm ? 'Chores' : 'Other';
        html += '<div class="subsection-block">';
        html += '<div class="subsection-header collapsible" data-section="chore">';
        html += '<span class="section-arrow">' + (choreExpanded ? '▼' : '▶') + '</span> ';
        html += choreLabel + ' (' + choreCommits.length + ')';
        html += '</div>';
        html += '<div class="section-body" style="' + (choreExpanded ? '' : 'display:none') + '">';
        for (var m = 0; m < choreCommits.length; m++) {
          html += renderCommitCardForCommit(choreCommits[m], analysisMap);
        }
        html += '</div></div>';
      }

      html += '</div></div>';
    }

    if (searchQuery) {
      html += '<div style="text-align:center;padding:16px 0;"><button class="cross-search-btn" id="crossSearchBtn">Search across all dates</button></div>';
    }

    $('#commitList').innerHTML = html;
    restoreExpanded();
  }

  function renderCommitCardForCommit(commit, analysisMap) {
    if (analysisMap) {
      var savedAnalysis = analysisData;
      analysisData = { commits: Object.values(analysisMap) };
      var card = renderCommitCard(commit);
      analysisData = savedAnalysis;
      return card;
    }
    return renderCommitCard(commit);
  }

  // ── Init ────────────────────────────────────────
  function init() {
    $$('.repo-tab').forEach((tab) => {
      tab.addEventListener('click', async () => {
        $$('.repo-tab').forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        currentRepo = tab.dataset.repo;
        activeFilter = 'all';
        searchQuery = '';
        crossDayResults = null;
        sectionsExpanded = { ascend: true, code: false, chore: false, 'needs-test': true, other: false, 'false-positive': true };
        $('#searchInput').value = '';
        $('#searchClear').style.display = 'none';
        $$('.filter-chip').forEach((c) => c.classList.remove('active'));
        $$('.filter-chip')[0].classList.add('active');
        // Reset arch impact index on repo switch
        archImpactIndex = null;
        await loadAvailableDates();
        await loadAdaptationStatus();
        await loadBaseline();
        currentDateIndex = 0;
        currentMonth = null;
        if (availableDates.length > 0) {
          await loadDate(availableDates[0]);
        }
      });
    });

    $('#dateBar').addEventListener('click', (e) => {
      const chip = e.target.closest('.calendar-cell:not(.empty)');
      if (chip) {
        const date = chip.dataset.date;
        const idx = availableDates.indexOf(date);
        if (idx !== -1) {
          currentDateIndex = idx;
          var parts = date.split('-');
          currentMonth = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, 1);
          loadDate(date);
        }
        return;
      }
      const prev = e.target.closest('#datePrev');
      if (prev && !prev.disabled) {
        var parts = prev.dataset.offset.split('-');
        currentMonth = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, 1);
        renderDateBar();
        return;
      }
      const next = e.target.closest('#dateNext');
      if (next && !next.disabled) {
        var parts = next.dataset.offset.split('-');
        currentMonth = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, 1);
        renderDateBar();
        return;
      }
    });

    $$('.filter-chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        $$('.filter-chip').forEach((c) => c.classList.remove('active'));
        chip.classList.add('active');
        activeFilter = chip.dataset.filter;
        render();
      });
    });

    $('#searchInput').addEventListener('input', (e) => {
      searchQuery = e.target.value;
      $('#searchClear').style.display = searchQuery ? 'flex' : 'none';
      crossDayResults = null;
      render();
    });

    $('#searchClear').addEventListener('click', () => {
      searchQuery = '';
      $('#searchInput').value = '';
      $('#searchClear').style.display = 'none';
      $('#searchInput').focus();
      crossDayResults = null;
      render();
    });

    document.addEventListener('click', (e) => {
      const header = e.target.closest('.commit-header');
      if (header && !e.target.closest('.commit-sha')) {
        const card = header.closest('.commit-card');
        card.classList.toggle('expanded');
        var expanded = [];
        document.querySelectorAll('.commit-card.expanded').forEach(function (c) {
          expanded.push(c.dataset.sha);
        });
        sessionStorage.setItem('vllmExpanded', JSON.stringify(expanded));
        return;
      }

      // Section header click (any section/sub-section)
      const sectionHeader = e.target.closest('[data-section]');
      if (sectionHeader && (sectionHeader.classList.contains('section-header') || sectionHeader.classList.contains('subsection-header'))) {
        var key = sectionHeader.dataset.section;
        sectionsExpanded[key] = !sectionsExpanded[key];
        render();
        return;
      }

      const crossBtn = e.target.closest('#crossSearchBtn');
      if (crossBtn) {
        searchAcrossDates();
        return;
      }

      const toggle = e.target.closest('.diff-toggle');
      if (toggle) {
        const sha = toggle.dataset.sha;
        const content = document.getElementById('diff-' + sha);
        toggle.classList.toggle('open');
        content.classList.toggle('open');
      }
    });

    $('#exportBtn').addEventListener('click', exportToExcel);

    // Set default date range to last 7 days
    var today = cnDateStr(new Date());
    var weekAgo = new Date(today + 'T00:00:00+08:00');
    weekAgo.setDate(weekAgo.getDate() - 6);
    $('#rangeStart').value = cnDateStr(weekAgo);
    $('#rangeEnd').value = today;

    $$('.filter-chip')[0].classList.add('active');
    detectDataBase().then(() => {
      loadAvailableDates().then(async () => {
        await loadAdaptationStatus();
        await loadBaseline();
        loadAnalysisDates().then(function () {
          currentDateIndex = 0;
          if (availableDates.length > 0) {
            loadDate(availableDates[0]);
          } else {
            showLoading(false);
            $('#emptyState').style.display = 'block';
          }
        });
      });
    });
  }

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();