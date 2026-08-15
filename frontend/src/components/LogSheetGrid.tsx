import { DUTY_ROWS, type DailyLog, type DutyStatus } from "../types";

/* Geometry. The SVG is authored at a fixed size and scaled by CSS, so every
   measurement below is in a single consistent unit. */
const WIDTH = 940;
const LABEL_W = 118; // left status-label column
const TOTALS_W = 58; // right totals column
const HEADER_H = 26; // hour numbers above the grid
const ROW_H = 34;
const REMARKS_MIN_H = 108;

const GRID_LEFT = LABEL_W;
const GRID_RIGHT = WIDTH - TOTALS_W;
const GRID_TOP = HEADER_H;
const GRID_BOTTOM = GRID_TOP + ROW_H * DUTY_ROWS.length;
const HOUR_W = (GRID_RIGHT - GRID_LEFT) / 24;

/* Remarks geometry. Labels are angled so a long place name fits under a narrow
   slice of the grid, but duty changes cluster -- arriving, working and leaving
   a stop can span twenty minutes, which is four units of x. Angled text alone
   then stacks three names on the same pixels. So labels are dealt into stacked
   lanes and the tick is drawn down to whichever lane its own label sits in. */
const REMARK_ANGLE = 55;
const REMARK_COS = Math.cos((REMARK_ANGLE * Math.PI) / 180);
const REMARK_SIN = Math.sin((REMARK_ANGLE * Math.PI) / 180);
const REMARK_CHAR_W = 4.55; // mean advance of the sheet's 9px label font
const REMARK_MAX_CHARS = 24;
const REMARK_TICK = 10; // tick drop below the grid before lane 0
const LANE_STEP = 24; // vertical drop per lane
const MIN_LABEL_GAP = 14; // min x before a repeated name counts as adjacent

/* Collision box around a label's baseline, measured in the rotated frame:
   `u` runs along the text, `v` across it. Both carry a unit of padding. */
const REMARK_ASCENT = 7.6;
const REMARK_DESCENT = 2.6;
const REMARK_END_GAP = 6; // clear space after a label before the next begins

const STATUS_COLOR: Record<DutyStatus, string> = {
  off_duty: "var(--off-duty)",
  sleeper_berth: "var(--sleeper-berth)",
  driving: "var(--driving)",
  on_duty: "var(--on-duty)",
};

const ROW_INDEX: Record<DutyStatus, number> = {
  off_duty: 0,
  sleeper_berth: 1,
  driving: 2,
  on_duty: 3,
};

const x = (hour: number) => GRID_LEFT + hour * HOUR_W;
const rowY = (status: DutyStatus) =>
  GRID_TOP + ROW_INDEX[status] * ROW_H + ROW_H / 2;

function hourLabel(hour: number): string {
  if (hour === 0 || hour === 24) return "MID";
  if (hour === 12) return "NOON";
  return String(hour);
}

/** Trim trailing zeros so the totals column reads 10, 1.75, 7.75, 4.5. */
function formatHours(value: number): string {
  return Number(value.toFixed(2)).toString();
}

/**
 * Builds the duty trace as a single continuous path — horizontal runs joined
 * by vertical risers at each status change, exactly as a driver draws it by
 * hand with one unbroken pen stroke.
 */
function buildTracePath(log: DailyLog): string {
  if (log.segments.length === 0) return "";

  const parts: string[] = [];
  log.segments.forEach((segment, index) => {
    const y = rowY(segment.status);
    if (index === 0) {
      parts.push(`M ${x(segment.start_hour)} ${y}`);
    } else {
      // Riser from the previous row up or down into this one.
      parts.push(`L ${x(segment.start_hour)} ${y}`);
    }
    parts.push(`L ${x(segment.end_hour)} ${y}`);
  });
  return parts.join(" ");
}

interface PlacedRemark {
  x: number;
  /** Stagger lane, or -1 when the label repeats the one beside it. */
  lane: number;
  label: string;
  title: string;
}

interface Box {
  u0: number;
  u1: number;
  v0: number;
  v1: number;
}

/** The rotated-frame footprint of a label anchored at (px, py). */
function labelBox(px: number, py: number, width: number): Box {
  const u = px * REMARK_COS + py * REMARK_SIN;
  const v = -px * REMARK_SIN + py * REMARK_COS;
  return {
    u0: u,
    u1: u + width + REMARK_END_GAP,
    v0: v - REMARK_ASCENT,
    v1: v + REMARK_DESCENT,
  };
}

const hits = (a: Box, b: Box) =>
  a.u0 < b.u1 && b.u0 < a.u1 && a.v0 < b.v1 && b.v0 < a.v1;

/**
 * Deals each remark into the topmost lane where its label touches nothing
 * already placed, and reports how deep the remarks area must be to hold them.
 *
 * Testing in the rotated frame rather than on x alone matters: angled labels
 * run down and to the right, so a label dropped one lane below a neighbour
 * also slides *along* that neighbour's baseline. Comparing x positions and
 * lane numbers separately says those two clear each other when they do not.
 *
 * A repeated place name is dropped rather than laned: the tick still records
 * the duty change, as Sec. 395.8 requires, but printing "Danao City" twice
 * fourteen minutes apart costs a lane and tells the reader nothing.
 */
function placeRemarks(remarks: DailyLog["remarks"]): {
  placed: PlacedRemark[];
  depth: number;
} {
  const taken: Box[] = [];
  let previous: { x: number; location: string } | null = null;
  let depth = 0;

  const placed = [...remarks]
    .sort((a, b) => a.hour - b.hour)
    .map((remark) => {
      const remarkX = x(remark.hour);
      const label =
        remark.location.length > REMARK_MAX_CHARS
          ? `${remark.location.slice(0, REMARK_MAX_CHARS - 1).trimEnd()}…`
          : remark.location;
      const title = remark.note
        ? `${remark.location} — ${remark.note}`
        : remark.location;

      if (
        previous &&
        previous.location === remark.location &&
        remarkX - previous.x < MIN_LABEL_GAP
      ) {
        return { x: remarkX, lane: -1, label, title };
      }

      const width = label.length * REMARK_CHAR_W;
      let lane = 0;
      let box = labelBox(remarkX, GRID_BOTTOM + REMARK_TICK + 4, width);
      while (taken.some((other) => hits(box, other))) {
        lane += 1;
        box = labelBox(
          remarkX,
          GRID_BOTTOM + REMARK_TICK + 4 + lane * LANE_STEP,
          width,
        );
      }
      taken.push(box);
      previous = { x: remarkX, location: remark.location };

      depth = Math.max(
        depth,
        lane * LANE_STEP + width * REMARK_SIN + REMARK_DESCENT,
      );
      return { x: remarkX, lane, label, title };
    });

  return { placed, depth };
}

interface Props {
  log: DailyLog;
}

export function LogSheetGrid({ log }: Props) {
  const hours = Array.from({ length: 25 }, (_, index) => index);
  const { placed, depth } = placeRemarks(log.remarks);
  // Never shorter than the original box, so a quiet day and a busy one still
  // sit at the same height side by side; only a genuinely deep stack grows it.
  const remarksH = Math.max(REMARKS_MIN_H, REMARK_TICK + depth + 12);
  const height = GRID_BOTTOM + remarksH;

  return (
    <svg
      className="sheet__grid"
      viewBox={`0 0 ${WIDTH} ${height}`}
      role="img"
      aria-label={`Driver's daily log grid for ${log.date}`}
    >
      {/* Alternating row bands, so the eye can follow a status across 24 hours */}
      {DUTY_ROWS.map((row, index) => (
        <rect
          key={row.status}
          x={GRID_LEFT}
          y={GRID_TOP + index * ROW_H}
          width={GRID_RIGHT - GRID_LEFT}
          height={ROW_H}
          fill={index % 2 === 0 ? "var(--surface)" : "var(--bg)"}
        />
      ))}

      {/* Quarter-hour ticks: short marks rising from each row's floor */}
      {hours.slice(0, 24).map((hour) =>
        [0.25, 0.5, 0.75].map((fraction) => {
          const tickX = x(hour + fraction);
          const isHalf = fraction === 0.5;
          return DUTY_ROWS.map((row, index) => {
            const bottom = GRID_TOP + (index + 1) * ROW_H;
            const length = isHalf ? 9 : 5;
            return (
              <line
                key={`${row.status}-${hour}-${fraction}`}
                x1={tickX}
                y1={bottom}
                x2={tickX}
                y2={bottom - length}
                stroke="var(--border-strong)"
                strokeWidth={0.75}
              />
            );
          });
        }),
      )}

      {/* Hour lines and their labels */}
      {hours.map((hour) => (
        <g key={`hour-${hour}`}>
          <line
            x1={x(hour)}
            y1={GRID_TOP}
            x2={x(hour)}
            y2={GRID_BOTTOM}
            stroke={hour % 6 === 0 ? "var(--ink-muted)" : "var(--border)"}
            strokeWidth={hour % 6 === 0 ? 1 : 0.75}
          />
          <text
            x={x(hour)}
            y={GRID_TOP - 8}
            textAnchor="middle"
            fontSize={hour === 0 || hour === 12 || hour === 24 ? 9 : 9.5}
            fontWeight={hour % 6 === 0 ? 700 : 500}
            fill={hour % 6 === 0 ? "var(--navy)" : "var(--ink-soft)"}
            fontFamily="var(--mono)"
          >
            {hourLabel(hour)}
          </text>
        </g>
      ))}

      {/* Row separators and the outer frame */}
      {DUTY_ROWS.map((row, index) => (
        <line
          key={`sep-${row.status}`}
          x1={GRID_LEFT}
          y1={GRID_TOP + index * ROW_H}
          x2={GRID_RIGHT}
          y2={GRID_TOP + index * ROW_H}
          stroke="var(--ink-muted)"
          strokeWidth={0.75}
        />
      ))}
      <rect
        x={GRID_LEFT}
        y={GRID_TOP}
        width={GRID_RIGHT - GRID_LEFT}
        height={GRID_BOTTOM - GRID_TOP}
        fill="none"
        stroke="var(--navy)"
        strokeWidth={1.25}
      />

      {/* Row labels */}
      {DUTY_ROWS.map((row) => (
        <text
          key={`label-${row.status}`}
          x={LABEL_W - 12}
          y={rowY(row.status) + 3.5}
          textAnchor="end"
          fontSize={10.5}
          fontWeight={600}
          fill="var(--ink-soft)"
        >
          {row.label}
        </text>
      ))}

      {/* The continuous pen stroke: risers plus a faint spine under the runs */}
      <path
        d={buildTracePath(log)}
        fill="none"
        stroke="var(--navy)"
        strokeWidth={1.5}
        strokeLinejoin="miter"
      />

      {/* Colour-coded duty runs drawn over the trace */}
      {log.segments.map((segment, index) => (
        <line
          key={`seg-${index}`}
          x1={x(segment.start_hour)}
          y1={rowY(segment.status)}
          x2={x(segment.end_hour)}
          y2={rowY(segment.status)}
          stroke={STATUS_COLOR[segment.status]}
          strokeWidth={4.5}
          strokeLinecap="butt"
        >
          <title>
            {`${segment.note || segment.status} — ${segment.location}`}
          </title>
        </line>
      ))}

      {/* Totals column */}
      <line
        x1={GRID_RIGHT}
        y1={GRID_TOP}
        x2={GRID_RIGHT}
        y2={GRID_BOTTOM}
        stroke="var(--navy)"
        strokeWidth={1.25}
      />
      {DUTY_ROWS.map((row) => (
        <text
          key={`total-${row.status}`}
          x={GRID_RIGHT + (TOTALS_W - 12) / 2}
          y={rowY(row.status) + 4}
          textAnchor="middle"
          fontSize={12}
          fontWeight={650}
          fill="var(--ink)"
          fontFamily="var(--mono)"
        >
          {formatHours(log.totals[row.status] ?? 0)}
        </text>
      ))}
      <text
        x={GRID_RIGHT + (TOTALS_W - 12) / 2}
        y={GRID_BOTTOM + 15}
        textAnchor="middle"
        fontSize={11.5}
        fontWeight={700}
        fill="var(--ink)"
        fontFamily="var(--mono)"
      >
        {`= ${formatHours(log.total_hours)}`}
      </text>
      <text
        x={GRID_RIGHT + (TOTALS_W - 12) / 2}
        y={GRID_TOP - 8}
        textAnchor="middle"
        fontSize={8}
        fontWeight={700}
        fill="var(--ink-soft)"
        letterSpacing="0.06em"
      >
        TOTAL
      </text>

      {/* Remarks: a tick at each duty change with the location, angled to fit */}
      <text
        x={GRID_LEFT - 12}
        y={GRID_BOTTOM + 15}
        textAnchor="end"
        fontSize={9.5}
        fontWeight={700}
        fill="var(--ink-soft)"
        letterSpacing="0.06em"
      >
        REMARKS
      </text>
      {placed.map((remark, index) => {
        // A suppressed duplicate keeps its tick but stops at lane 0's depth.
        const lane = Math.max(remark.lane, 0);
        const tickEnd = GRID_BOTTOM + REMARK_TICK + lane * LANE_STEP;
        const textY = tickEnd + 4;
        return (
          <g key={`remark-${index}`}>
            <line
              x1={remark.x}
              y1={GRID_BOTTOM}
              x2={remark.x}
              y2={tickEnd}
              stroke="var(--ink-muted)"
              strokeWidth={0.75}
            />
            {remark.lane >= 0 && (
              <text
                x={remark.x}
                y={textY}
                fontSize={9}
                fill="var(--ink-soft)"
                transform={`rotate(${REMARK_ANGLE} ${remark.x} ${textY})`}
              >
                {remark.label}
                <title>{remark.title}</title>
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
