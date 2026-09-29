import MetricsStrip from './MetricsStrip.jsx'
import ProfilesTable from './ProfilesTable.jsx'
import TrafficPanel from './TrafficPanel.jsx'

export default function AnalyticsCard({ metrics, lineCounters, zoneEvents, profiles }) {
  return (
    <details className="analytics-card">
      <summary className="analytics-card-summary">&#9658; Analytics</summary>
      <div className="analytics-card-body">
        <MetricsStrip metrics={metrics} />
        <TrafficPanel lineCounters={lineCounters} zoneEvents={zoneEvents} />
        <ProfilesTable profiles={profiles} />
      </div>
    </details>
  )
}
