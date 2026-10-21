# Picture links for QR codes (build 128)

A QR code only fits a 64 px copy of a station's picture, so it also carries a
link to the full picture. Pictures found on the web already have a link. A
picture you or your users pick from their own files is uploaded, when it's
shared, to your own small Cloudflare service (art-worker.js in this folder).

## Set it up once (about 10 minutes, in a web browser)

1. Make a free account at https://dash.cloudflare.com/sign-up and sign in.
2. In the left menu, open **R2 Object Storage** and turn it on. Cloudflare may
   ask for a card. The free amount is 10 GB of pictures (about 100,000), with no
   charge for people viewing them; you're only billed above that.
3. Press **Create bucket**, name it `frequencies-art`, and press **Create bucket**.
4. In the left menu, open **Workers & Pages**, press **Create**, then **Create
   Worker** (or "Start with Hello World"). Name it `frequencies-art` and press
   **Deploy**.
5. Press **Edit code**. Delete everything in the editor, then open
   `cloudflare/art-worker.js` from this folder in a text editor, copy all of
   it, and paste it in. Press **Deploy**.
6. Go back to the Worker, open **Settings**, then **Bindings**, press **Add**,
   choose **R2 bucket**. Variable name: `ART` (capital letters). Bucket:
   `frequencies-art`. Press **Deploy** (or Save).
7. Copy the Worker's address from its page. It looks like
   `https://frequencies-art.<your name>.workers.dev`. Opening it in a browser
   should say "Frequencies picture links."
8. In your **dev build**: Settings > Lyrics and artists > **Picture link
   address**, paste the address, press Enter.
9. Build the user AppImage and the Windows installer again, as usual. The build
   copies the address in, so your users' builds can upload too.

## Taking a picture down

Open R2 Object Storage > `frequencies-art`, find the file named in the link
(the part after `/a/`), and delete it.

## Limits

- Pictures only (PNG, JPEG, GIF, WebP), 2 MB at most. The app shrinks bigger
  ones first.
- The same picture always gets the same link, so sharing it again stores
  nothing new.
- The address isn't a secret. Anyone who finds it could upload pictures, but
  only pictures, and only 2 MB at a time. To limit how often one person can
  upload, add a rate limit binding named `UPLOAD_LIMIT` to the Worker later
  (optional; the Worker uses it when it's there).
- Uploading a picture puts it on the internet: anyone with the link can see it.
