const INVENTORY_URL = 'https://storage.googleapis.com/parquet.gtfsrt.io/inventory.json';

document.addEventListener('DOMContentLoaded', fetchInventory);

async function fetchInventory() {
  const container = document.getElementById('inventory-content');

  try {
    const response = await fetch(INVENTORY_URL);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const feeds = await response.json();
    renderStats(computeStats(feeds));
    renderInventory(container, groupByAgency(feeds));
    populateFeedSelector(feeds);
  } catch (error) {
    showError(container, error);
  }
}

function computeStats(feeds) {
  const agencyIds = new Set(feeds.map(f => f.agency_id));
  const totalRecords = feeds.reduce((sum, f) => sum + f.total_records, 0);
  const totalBytes = feeds.reduce((sum, f) => sum + f.total_bytes, 0);
  const dateMin = feeds.reduce((min, f) => f.date_min < min ? f.date_min : min, feeds[0].date_min);
  const dateMax = feeds.reduce((max, f) => f.date_max > max ? f.date_max : max, feeds[0].date_max);

  return {
    agencies: agencyIds.size,
    feeds: feeds.length,
    totalRecords,
    totalBytes,
    dateMin,
    dateMax,
  };
}

function renderStats(stats) {
  document.getElementById('stat-agencies').textContent = stats.agencies;
  document.getElementById('stat-feeds').textContent = stats.feeds;
  document.getElementById('stat-records').textContent = formatNumber(stats.totalRecords);
  document.getElementById('stat-size').textContent = formatBytes(stats.totalBytes);
  document.getElementById('stat-range').textContent = `${formatDate(stats.dateMin)} \u2013 ${formatDate(stats.dateMax)}`;
}

function groupByAgency(feeds) {
  const agencies = new Map();

  for (const feed of feeds) {
    const key = feed.agency_id;
    if (!agencies.has(key)) {
      agencies.set(key, {
        id: feed.agency_id,
        name: feed.agency_name,
        feeds: [],
      });
    }
    agencies.get(key).feeds.push(feed);
  }

  return [...agencies.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function renderInventory(container, agencies) {
  container.innerHTML = agencies.map(agency => {
    const totalRecords = agency.feeds.reduce((s, f) => s + f.total_records, 0);
    const totalBytes = agency.feeds.reduce((s, f) => s + f.total_bytes, 0);
    const hasMultipleSystems = new Set(agency.feeds.map(f => f.system_id).filter(Boolean)).size > 1;

    const sortedFeeds = [...agency.feeds].sort((a, b) => {
      const sysCompare = (a.system_name || '').localeCompare(b.system_name || '');
      if (sysCompare !== 0) return sysCompare;
      return feedTypeSortOrder(a.feed_type) - feedTypeSortOrder(b.feed_type);
    });

    return `
      <details class="agency-card">
        <summary>
          ${escapeHtml(agency.name)}
          <span class="agency-card-meta">
            <span>${agency.feeds.length} feeds</span>
            <span>${formatNumber(totalRecords)} records</span>
            <span>${formatBytes(totalBytes)}</span>
          </span>
        </summary>
        <div class="agency-card-body">
          <div class="feed-table-wrap">
            <table class="feed-table">
              <thead>
                <tr>
                  <th>Feed Type</th>
                  ${hasMultipleSystems ? '<th>System</th>' : ''}
                  <th>Date Range</th>
                  <th style="text-align:right">Records</th>
                  <th style="text-align:right">Size</th>
                </tr>
              </thead>
              <tbody>
                ${sortedFeeds.map(f => `
                  <tr>
                    <td><span class="badge ${badgeClass(f.feed_type)}">${feedTypeLabel(f.feed_type)}</span></td>
                    ${hasMultipleSystems ? `<td>${escapeHtml(f.system_name || '\u2014')}</td>` : ''}
                    <td>${formatDate(f.date_min)} &ndash; ${formatDate(f.date_max)}</td>
                    <td class="num">${formatNumber(f.total_records)}</td>
                    <td class="num">${formatBytes(f.total_bytes)}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </details>
    `;
  }).join('');
}

// Feed selector for code examples

function populateFeedSelector(feeds) {
  const select = document.getElementById('feed-select');
  const agencies = groupByAgency(feeds);

  for (const agency of agencies) {
    const group = document.createElement('optgroup');
    group.label = agency.name;

    const sortedFeeds = [...agency.feeds].sort((a, b) => {
      const sysCompare = (a.system_name || '').localeCompare(b.system_name || '');
      if (sysCompare !== 0) return sysCompare;
      return feedTypeSortOrder(a.feed_type) - feedTypeSortOrder(b.feed_type);
    });

    for (const feed of sortedFeeds) {
      const option = document.createElement('option');
      const systemLabel = feed.system_name ? ` / ${feed.system_name}` : '';
      option.textContent = `${feedTypeLabel(feed.feed_type)}${systemLabel}`;
      option.value = JSON.stringify({
        feed_type: feed.feed_type,
        base64url: feed.base64url,
        date: feed.date_max,
      });
      group.appendChild(option);
    }

    select.appendChild(group);
  }

  select.disabled = false;
  select.addEventListener('change', onFeedSelect);
}

function onFeedSelect(e) {
  const select = e.target;
  if (!select.value) {
    updateExamples('<feed_type>', '<date>', '<base64url>');
    return;
  }

  const { feed_type, base64url, date } = JSON.parse(select.value);
  updateExamples(feed_type, date, base64url);
}

function updateExamples(feedType, date, base64url) {
  document.getElementById('example-duckdb').textContent =
`INSTALL httpfs;
LOAD httpfs;

SELECT *
FROM read_parquet(
  'http://parquet.gtfsrt.io/${feedType}/date=${date}/base64url=${base64url}/data.parquet',
  hive_partitioning = true
)
LIMIT 100;`;

  document.getElementById('example-python').textContent =
`import pandas as pd

df = pd.read_parquet(
    "http://parquet.gtfsrt.io/${feedType}"
    "/date=${date}"
    "/base64url=${base64url}"
    "/data.parquet"
)
print(df.head())`;

  document.getElementById('example-download').textContent =
`# Parquet files (compacted daily)
http://parquet.gtfsrt.io/${feedType}/date=${date}/base64url=${base64url}/data.parquet

# Raw protobuf snapshots
http://protobuf.gtfsrt.io/${feedType}/date=${date}/hour={ISO_HOUR}/base64url=${base64url}/{timestamp}.pb`;
}

function showError(container, error) {
  container.innerHTML = `
    <div class="error-message">
      <p>Could not load the feed inventory.</p>
      <p>You can view it directly at
        <a href="${INVENTORY_URL}" target="_blank" rel="noopener">inventory.json</a>
      </p>
      <button onclick="fetchInventory()">Try Again</button>
    </div>
  `;
}

// Formatting helpers

function formatNumber(n) {
  if (n >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1) + 'B';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return n.toLocaleString();
}

function formatBytes(bytes) {
  if (bytes >= 1_000_000_000) return (bytes / 1_000_000_000).toFixed(1) + ' GB';
  if (bytes >= 1_000_000) return (bytes / 1_000_000).toFixed(1) + ' MB';
  if (bytes >= 1_000) return (bytes / 1_000).toFixed(1) + ' KB';
  return bytes + ' B';
}

function formatDate(iso) {
  const [y, m, d] = iso.split('-');
  return `${m}/${d}`;
}

function feedTypeLabel(type) {
  const labels = {
    vehicle_positions: 'Vehicle Positions',
    trip_updates: 'Trip Updates',
    service_alerts: 'Service Alerts',
  };
  return labels[type] || type;
}

function badgeClass(type) {
  const classes = {
    vehicle_positions: 'badge-vehicle',
    trip_updates: 'badge-trips',
    service_alerts: 'badge-alerts',
  };
  return classes[type] || '';
}

function feedTypeSortOrder(type) {
  const order = { vehicle_positions: 0, trip_updates: 1, service_alerts: 2 };
  return order[type] ?? 3;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
