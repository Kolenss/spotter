import { useCallback, useEffect, useRef, useState } from "react";
import { Marker, Tooltip, useMapEvents } from "react-leaflet";
import L from "leaflet";
import {
  MIN_ZOOM,
  NAMED_ONLY_ABOVE,
  OverpassBusy,
  TRUCK_STOP_LABEL,
  boxKey,
  fetchTruckStops,
  snapBox,
  type TruckStop,
  type TruckStopKind,
} from "../truckStops";

export type LayerStatus =
  | "off"
  | "idle"
  | "loading"
  | "zoomed_out"
  | "busy"
  | "empty"
  | "named_only";

/** Overpass takes 2s on a small box and much longer on a large one, so the
 *  request waits for the pan to actually finish rather than chasing it. */
const SETTLE_MS = 600;

const KIND_COLOR: Record<TruckStopKind, string> = {
  truck_stop: "#0f766e",
  fuel: "#0f766e",
  parking: "#64748b",
  rest_area: "#64748b",
};

/* Cached per appearance for the reason documented in PickerMap: react-leaflet
   calls setIcon() on identity change, which replaces the marker's DOM node. */
const ICON_CACHE = new Map<string, L.DivIcon>();

function stopIcon(kind: TruckStopKind, named: boolean) {
  const key = `${kind}|${named}`;
  let icon = ICON_CACHE.get(key);
  if (!icon) {
    icon = L.divIcon({
      className: "stopicon",
      html: `<span class="${named ? "is-named" : ""}" style="background:${KIND_COLOR[kind]}"></span>`,
      iconSize: [12, 12],
      iconAnchor: [6, 6],
    });
    ICON_CACHE.set(key, icon);
  }
  return icon;
}

interface Props {
  enabled: boolean;
  onStatus: (status: LayerStatus) => void;
  onPick: (stop: TruckStop) => void;
}

export function TruckStopLayer({ enabled, onStatus, onPick }: Props) {
  const [stops, setStops] = useState<TruckStop[]>([]);
  const [key, setKey] = useState<string | null>(null);

  const timer = useRef<number | undefined>(undefined);
  const request = useRef<AbortController | undefined>(undefined);
  /** The box being fetched, kept out of state so the effect does not re-run. */
  const pending = useRef<ReturnType<typeof snapBox> | null>(null);

  const map = useMapEvents({
    moveend: () => schedule(),
    zoomend: () => schedule(),
  });

  const schedule = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (map.getZoom() < MIN_ZOOM) {
        request.current?.abort();
        setStops([]);
        setKey(null);
        onStatus("zoomed_out");
        return;
      }
      const bounds = map.getBounds();
      const box = snapBox({
        south: bounds.getSouth(),
        west: bounds.getWest(),
        north: bounds.getNorth(),
        east: bounds.getEast(),
      });
      if (!box) {
        // Straddling the antimeridian; nothing sensible to ask for.
        setStops([]);
        setKey(null);
        onStatus("empty");
        return;
      }
      pending.current = box;
      setKey(boxKey(box));
    }, SETTLE_MS);
  }, [map, onStatus]);

  // Also runs on mount and whenever the layer is switched back on, so turning
  // it on while already zoomed in fetches without waiting for a pan.
  useEffect(() => {
    if (!enabled) {
      window.clearTimeout(timer.current);
      request.current?.abort();
      setStops([]);
      setKey(null);
      onStatus("off");
      return;
    }

    /* Switching the layer on is a request to see stops, and the map opens on
       the whole country -- far too wide to query. Left alone the button looks
       broken: you press it and the only thing that happens is a line of grey
       text. So meet the request halfway and zoom to where stops can load. The
       resulting zoomend schedules the fetch. */
    if (map.getZoom() < MIN_ZOOM) {
      map.setZoom(MIN_ZOOM);
      return;
    }

    schedule();
    return () => window.clearTimeout(timer.current);
  }, [enabled, map, schedule, onStatus]);

  useEffect(() => {
    if (!enabled || key === null || !pending.current) return;

    // Only one request at a time: Overpass allows two slots per address and a
    // queued one is rejected outright, so an obsolete pan must yield its slot.
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;

    onStatus("loading");
    fetchTruckStops(pending.current, controller.signal)
      .then((found) => {
        if (controller.signal.aborted) return;
        setStops(found);
        if (!found.length) onStatus("empty");
        else onStatus(found.length > NAMED_ONLY_ABOVE ? "named_only" : "idle");
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setStops([]);
        onStatus(error instanceof OverpassBusy ? "busy" : "empty");
      });

    return () => controller.abort();
  }, [enabled, key, onStatus]);

  if (!enabled) return null;

  /* Zoomed out over a whole region the unnamed lay-bys and truck bays outnumber
     the real stops and merge into an unclickable smear. The named ones are what
     a driver is aiming for, so past this density they are all that is drawn. */
  const visible =
    stops.length > NAMED_ONLY_ABOVE ? stops.filter((stop) => stop.named) : stops;

  return (
    <>
      {visible.map((stop) => (
        <Marker
          key={stop.id}
          position={[stop.lat, stop.lon]}
          icon={stopIcon(stop.kind, stop.named)}
          eventHandlers={{ click: () => onPick(stop) }}
        >
          <Tooltip direction="top" offset={[0, -8]}>
            <strong>{stop.label}</strong>
            {stop.named && (
              <>
                <br />
                {TRUCK_STOP_LABEL[stop.kind]}
              </>
            )}
          </Tooltip>
        </Marker>
      ))}
    </>
  );
}
