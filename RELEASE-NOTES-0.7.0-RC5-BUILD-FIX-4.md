# SG-AWG-Panel 0.7.0-RC5 Build Fix 4

UI-only update. Server, clients, Cluster, Cascade routing and AmneziaWG runtime logic are unchanged.

## Latte Graphite

- Added a complete light theme for the whole web panel.
- Added the global selector `Тёмная / Latte Graphite / Как в системе` in the top bar.
- The choice is stored in the browser and is applied before the page is drawn, without a bright flash.
- `Как в системе` follows the browser/Windows light or dark preference and reacts when it changes.
- Approved palette: page `#E3E9EE`, cards `#EEF2F4`, nested blocks `#D9E2E8`, fields `#EAF0F3`, borders `#AEBCC7`, text `#192530`, muted text `#556672`, buttons `#31536F`.
- Status colors, active navigation, dialogs, tables, forms, Cascade, Clients, Cluster, Security, Maintenance, Help and the login page remain contrast-safe.

## Cascade layout

- Reduced the opened `Ссылка готова` block: smaller inner spacing, shorter link field and copy button.
- Server/Endpoint information is pulled closer to the link.
- The lower edges of the Outbound and Inbound cards are visually closer and `Технические детали` remains visible without extra scrolling.

## Identification

- UI build: `sgawg070rc5bf4`.
