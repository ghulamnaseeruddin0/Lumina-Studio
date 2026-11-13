# Music library (optional but recommended)

Drop royalty-free tracks here (.mp3 .wav .m4a .ogg .flac). Lumina picks one that matches the video's mood
and loops it under the narration. Without any file it makes a soft synthetic ambient pad instead.

**Mood by folder or file name:** put files in folders named `calm`, `dark`, `upbeat`, `epic`, `inspiring`, `news`
(or include the word in the file name). Files that match the video's mood are preferred.

**Where to get free music:** YouTube Audio Library (in YouTube Studio), Pixabay Music, and Kevin MacLeod's Incompetech
(requires credit). Always read each track's licence.

**Credits:** create `credits.json` in this folder so the licence is written into your video description:

```json
{
  "my-track.mp3": {"title": "My Track", "artist": "Artist Name", "license": "CC BY 4.0", "url": "https://example.com/my-track"}
}
```
