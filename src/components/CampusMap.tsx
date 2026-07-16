import { BatteryMedium, Bot, MapPin } from "lucide-react";
import { getLocation, locations } from "../data/demo";
import type { Delivery, Robot } from "../types";

interface CampusMapProps {
  delivery?: Delivery;
  robot?: Robot;
  className?: string;
}

const clamp = (value: number, min = 0, max = 1) => Math.min(max, Math.max(min, value));

export function CampusMap({ delivery, robot, className = "" }: CampusMapProps) {
  const source = delivery ? getLocation(delivery.sourceId) : undefined;
  const destination = delivery ? getLocation(delivery.destinationId) : undefined;
  const fallbackLocation = robot ? getLocation(robot.locationId) : undefined;
  const routeProgress = delivery
    ? delivery.status === "TO_SOURCE" ? clamp((delivery.progress - 20) / 20)
      : ["AT_SOURCE", "PACKAGE_LOADED"].includes(delivery.status) ? 0
      : ["TO_DESTINATION", "AT_DESTINATION", "DELIVERED"].includes(delivery.status) ? clamp((delivery.progress - 50) / 40)
      : delivery.status === "COMPLETED" ? 1 : 0.35
    : 0;
  const start = delivery?.status === "TO_SOURCE" ? fallbackLocation ?? source : source ?? fallbackLocation;
  const end = delivery?.status === "TO_SOURCE" ? source : destination ?? fallbackLocation;
  const robotX = start && end ? start.x + (end.x - start.x) * routeProgress : fallbackLocation?.x ?? 15;
  const robotY = start && end ? start.y + (end.y - start.y) * routeProgress : fallbackLocation?.y ?? 76;

  return (
    <div className={`campus-map ${className}`}>
      <div className="map-grid" />
      <svg viewBox="0 0 100 100" role="img" aria-label="MIIT campus delivery route map">
        <defs>
          <filter id="map-shadow" x="-50%" y="-50%" width="200%" height="200%">
            <feDropShadow dx="0" dy="1.5" stdDeviation="1.2" floodOpacity=".18" />
          </filter>
        </defs>
        <path className="campus-road campus-road-wide" d="M14 77 C23 68 24 47 29 31 S47 23 50 19 S62 23 67 34 S73 52 81 62" />
        <path className="campus-road" d="M29 31 C37 38 34 51 30 58 S43 69 54 72 S67 67 81 62" />
        <path className="campus-road" d="M30 58 C43 49 54 44 67 34" />
        {source && destination && (
          <>
            <line className="route-shadow" x1={source.x} y1={source.y} x2={destination.x} y2={destination.y} />
            <line className="active-route" x1={source.x} y1={source.y} x2={destination.x} y2={destination.y} />
          </>
        )}
        {locations.map((location) => {
          const isSource = source?.id === location.id;
          const isDestination = destination?.id === location.id;
          return (
            <g key={location.id} className={`map-location ${isSource ? "source" : ""} ${isDestination ? "destination" : ""}`}>
              <circle cx={location.x} cy={location.y} r={isSource || isDestination ? 2.5 : 1.7} />
              <text x={location.x} y={location.y + (location.y > 65 ? -4 : 5)} textAnchor="middle">{location.shortName}</text>
            </g>
          );
        })}
        <g className="robot-marker" transform={`translate(${robotX} ${robotY})`} filter="url(#map-shadow)">
          <circle r="5.4" />
          <path d="M-2.8-1.8h5.6v4.4h-5.6zM0-4v2.2M-2-4h4" />
          <circle cx="-1.3" cy=".2" r=".45" /><circle cx="1.3" cy=".2" r=".45" />
        </g>
      </svg>
      <div className="map-legend">
        <span><i className="legend-dot source-dot" />Source</span>
        <span><i className="legend-dot destination-dot" />Destination</span>
        <span><Bot size={14} />Live robot</span>
      </div>
      {delivery && (
        <div className="map-mission-chip">
          <div className="chip-icon"><MapPin size={16} /></div>
          <div><span>Live mission</span><strong>{delivery.trackingCode}</strong></div>
          {robot && <div className="chip-battery"><BatteryMedium size={15} />{robot.battery}%</div>}
        </div>
      )}
    </div>
  );
}
