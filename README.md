# JOP

**Your closet, audited.** JOP turns everything you've bought to wear into a zoomable five-year timeline, shows where the money went, and has an opinionated agent that tells you what to buy next, including what *not* to buy on Black Friday.

A tongue-in-cheek homage to Shop. Not affiliated.

![A tour of JOP: the workflow hero, order emails becoming wardrobe items, the 5-year timeline, an item's detail, the Black Friday basket and Jop Agent](docs/jop-tour.gif)

## What it does

- **Five-year timeline.** Every purchase sits on the day you bought it, with a photo. Drag to pan, zoom with the slider or `⌘/Ctrl` + scroll, and click a piece for its details. Purchases made on the same day sit side by side.
- **Item records.** Name, brand, category, size, colour, price, purchase date, store, material, notes, a wear counter and a photo, stored in SQLite.
- **Still in your life?** Mark a piece as *not feeling it*, *not available* or *disposed*, with a note. Those pieces stop counting toward your wardrobe, and their cost adds up in the "regret fund".
- **Where it all went.** Monthly spend with a running total, and spend per category.
- **Black Friday, supervised.** Set a budget and Jop reviews your wardrobe against a 24-piece capsule, ranks the deals by what you actually need, shows the cost per wear, and blocks the ones you'd regret (the Impulse Shield).
- **Jop Agent.** Suggests the next purchase per category and can add it to your timeline. Autopilot on means it doesn't ask first.
- **A hero that explains the job.** "Hand Jop your card", with the workflow front and centre as a path that zig-zags up and down through six steps: read the order emails in your Gmail export, pull out item, size, brand, price and date, rebuild your wardrobe (with your sizes and favourite brand), have you confirm what you still own, add your card (and then you won't know what it buys), and shop Black Friday at 00:00 from the vendors you already use. Order emails turn from *processing* to *processed* in step 1 as their items appear in step 3. As a small extra, a rack of 30 identical black tees you can click to discard.

## Run it

Needs Node 22.13 or newer. No dependencies to install.

```sh
npm start
```

Open <http://127.0.0.1:3000>. Set `PORT` to use another port.

The first run creates `data/jop.db` and fills it with a sample wardrobe: 49 pieces from November 2021 to November 2026, with photos. The footer's *Reset sample data* link restores it.

## Your data stays local

- The server listens on `127.0.0.1` only and has no login, so don't expose it to a network.
- The SQLite database and uploaded photos live in `data/`, which git ignores.
- Put Gmail exports and anything derived from email in `gmail/` (`exports/` and `processing/`). It is git-ignored, along with `*.mbox`, `*.eml`, `*.zip` and `Takeout/`.

## API

JSON over HTTP. Writes must send `Content-Type: application/json`.

| Method | Path | |
| --- | --- | --- |
| `GET` | `/api/items` | List items, oldest first |
| `POST` | `/api/items` | Create an item |
| `GET` `PUT` `DELETE` | `/api/items/:id` | Read, update (partial) or delete |
| `POST` | `/api/items/:id/wear` | Add one to the wear count |
| `POST` | `/api/items/:id/photo` | Upload a JPEG, PNG or WebP (max 5 MB) as the raw body |
| `POST` | `/api/reset` | Wipe everything and restore the sample wardrobe |

Item fields: `name`, `brand`, `cat` (Outerwear, Tops, Knitwear, Bottoms, Shoes, Accessories), `size`, `color`, `price`, `date`, `store`, `material`, `notes`, `wears`, `status` (`active`, `disliked`, `unavailable`, `disposed`) and `statusNote`.

## Layout

```
server.js   HTTP server, SQLite schema, validation
seed.js     sample wardrobe (versioned; new sample rows are added to existing databases once)
public/     the whole UI: index.html, plus assets/ (generated product photos and mascot)
docs/       the GIF above
data/       your database and uploads (ignored)
gmail/      Gmail exports and processing (ignored)
```

## Still a mockup

- Black Friday deals are invented. No retailer is connected, and *Queue with Jop Agent* only shows a message.
- Gmail import and card checkout are not built into the app. The hero describes where it's heading, and the vendor names in it are plain text, not real logos.
- Product photos are AI-generated.
