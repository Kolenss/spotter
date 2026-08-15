import { useCallback, useEffect, useRef } from "react";
import { useMap } from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";

interface Props {
  /** Null while there is nothing to frame yet. */
  bounds: LatLngBoundsExpression | null;
  /**
   * Refit when this changes, and only then. Keeping it stable lets the driver
   * pan and zoom without the view being yanked back on every render -- which
   * matters on the picker, where they are working the map by hand.
   */
  fitKey?: string | number;
}

/**
 * Keeps the content framed, and keeps Leaflet's internal size in step with its
 * container.
 *
 * Leaflet caches the container's dimensions when the map is created. If it is
 * built before layout settles -- a hidden tab, a panel that animates open, a
 * flex child that has not been measured yet -- it caches the wrong size and
 * renders into a fraction of the frame forever. `invalidateSize()` is the only
 * thing that re-reads it, so a ResizeObserver drives both that and the refit.
 *
 * Shared by the route map and the location picker: both mount inside panels
 * that resize, so both hit this.
 */
export function MapFrame({ bounds, fitKey }: Props) {
  const map = useMap();

  // Held in a ref so the resize handler always sees current bounds without
  // being torn down and rebuilt every time they change identity.
  const latest = useRef(bounds);
  latest.current = bounds;

  const fit = useCallback(() => {
    if (!latest.current) return;
    // A single point makes degenerate bounds, which Leaflet fits by zooming to
    // its maximum -- one building, filling the frame. Capping keeps a lone pin
    // at roughly city scale, which is where the next one is likely to be. Any
    // real route fits well below this, so the cap never binds on the route map.
    map.fitBounds(latest.current, { padding: [36, 36], maxZoom: 12 });
  }, [map]);

  useEffect(() => {
    fit();
  }, [fit, fitKey]);

  useEffect(() => {
    const container = map.getContainer();
    let previous = `${container.clientWidth}x${container.clientHeight}`;

    const observer = new ResizeObserver(() => {
      const current = `${container.clientWidth}x${container.clientHeight}`;
      // Guard against re-entering on our own fitBounds, which does not change
      // the container's size.
      if (current === previous || container.clientWidth === 0) return;
      previous = current;
      map.invalidateSize();
      fit();
    });

    observer.observe(container);
    return () => observer.disconnect();
  }, [fit, map]);

  return null;
}
