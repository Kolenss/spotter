import { useEffect, useId, useState } from "react";
import { searchPlaces } from "../api";
import type { PlaceSuggestion } from "../types";

/* Nominatim's usage policy asks for at most one request a second and
   discourages autocomplete outright. A 500 ms debounce plus a three-character
   floor keeps a typed city to roughly one request, and the server caches
   repeats, so re-typing the same place costs nothing. */
const DEBOUNCE_MS = 500;
const MIN_QUERY_LENGTH = 3;

interface Props {
  id: string;
  label: string;
  placeholder: string;
  note: string;
  value: string;
  /** Set once the location is resolved to a point; null while it is free text. */
  pinned: { lat: number; lon: number } | null;
  error?: string;
  showRequired: boolean;
  /** Typed text. Clears any pin, because the words no longer describe it. */
  onType: (value: string) => void;
  onPick: (place: PlaceSuggestion) => void;
  onFocus: () => void;
  /** True when a map click would fill this field. */
  active: boolean;
}

export function LocationField({
  id,
  label,
  placeholder,
  note,
  value,
  pinned,
  error,
  showRequired,
  onType,
  onPick,
  onFocus,
  active,
}: Props) {
  const [suggestions, setSuggestions] = useState<PlaceSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const [searching, setSearching] = useState(false);

  const listboxId = useId();

  useEffect(() => {
    /* Once resolved, the text *is* the chosen place's label -- searching for it
       would spend a request to rediscover what we already have, and offer a
       dropdown of near-identical rows. Typing clears the pin, which resumes
       searching on the next render. Keyed on `pinned` rather than on how the
       value arrived, so picking from the dropdown and pinning on the map both
       take this path. */
    if (pinned) {
      setSuggestions([]);
      setOpen(false);
      return;
    }

    const query = value.trim();
    if (query.length < MIN_QUERY_LENGTH) {
      setSuggestions([]);
      setOpen(false);
      return;
    }

    const controller = new AbortController();
    setSearching(true);
    const timer = window.setTimeout(async () => {
      const results = await searchPlaces(query, controller.signal);
      if (controller.signal.aborted) return;
      setSuggestions(results);
      setHighlighted(-1);
      setOpen(results.length > 0);
      setSearching(false);
    }, DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
      // Abandons the in-flight request too, so a slow early query cannot land
      // after a fast later one and repopulate the list with stale matches.
      controller.abort();
      setSearching(false);
    };
  }, [value, pinned]);

  function choose(place: PlaceSuggestion) {
    onPick(place);
    setOpen(false);
    setSuggestions([]);
    setHighlighted(-1);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }

    if (!open || suggestions.length === 0) {
      // Let ArrowDown reopen a list the driver dismissed without retyping.
      if (event.key === "ArrowDown" && suggestions.length > 0) {
        event.preventDefault();
        setOpen(true);
      }
      return;
    }

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setHighlighted((index) => (index + 1) % suggestions.length);
        break;
      case "ArrowUp":
        event.preventDefault();
        setHighlighted(
          (index) => (index - 1 + suggestions.length) % suggestions.length,
        );
        break;
      case "Home":
        event.preventDefault();
        setHighlighted(0);
        break;
      case "End":
        event.preventDefault();
        setHighlighted(suggestions.length - 1);
        break;
      case "Enter":
        if (highlighted >= 0) {
          // Only swallow the submit when a suggestion is actually selected.
          event.preventDefault();
          choose(suggestions[highlighted]);
        }
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  }

  const invalid = showRequired || Boolean(error);

  return (
    <div className={`field field--location${active ? " field--active" : ""}`}>
      <label className="field__label" htmlFor={id}>
        {label}
        {pinned && (
          <span className="field__pin" title="Resolved to an exact point">
            pinned
          </span>
        )}
      </label>

      <div className="combo">
        <input
          id={id}
          type="text"
          autoComplete="off"
          placeholder={placeholder}
          value={value}
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-activedescendant={
            open && highlighted >= 0 ? `${listboxId}-${highlighted}` : undefined
          }
          aria-invalid={invalid}
          aria-describedby={`${id}-note`}
          onChange={(event) => onType(event.target.value)}
          onFocus={onFocus}
          // Delayed so a click on a suggestion registers before the list closes.
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onKeyDown={handleKeyDown}
        />
        {searching && <span className="combo__spinner" aria-hidden="true" />}

        <ul
          className="combo__list"
          id={listboxId}
          role="listbox"
          aria-label={`${label} suggestions`}
          hidden={!open}
        >
          {suggestions.map((place, index) => (
            <li
              key={`${place.lat},${place.lon}`}
              id={`${listboxId}-${index}`}
              role="option"
              aria-selected={index === highlighted}
              className={`combo__option${index === highlighted ? " is-active" : ""}`}
              // Mouse down rather than click: blur would close the list first.
              onMouseDown={(event) => {
                event.preventDefault();
                choose(place);
              }}
              onMouseEnter={() => setHighlighted(index)}
            >
              {place.label}
            </li>
          ))}
        </ul>
      </div>

      <span className="field__note" id={`${id}-note`}>
        {note}
      </span>
      {showRequired && (
        <span className="field__error">This location is required.</span>
      )}
      {error && <span className="field__error">{error}</span>}
    </div>
  );
}
