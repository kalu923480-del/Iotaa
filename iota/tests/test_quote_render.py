"""Tests for the /q quote card renderer (utils.quote_render)."""
import io
import unittest

from utils.quote_render import render_quote_card, QuoteRenderError, _has_glyph


class QuoteRenderTest(unittest.TestCase):

    def _basic(self, **kw):
        msgs = [{"name": "Alice", "text": "Hello world, this is a quote! 🔥"}]
        return render_quote_card(msgs, None, **kw)

    def test_png_renders(self):
        out = self._basic(mode="png", theme="dark")
        self.assertTrue(out.startswith(b"\x89PNG"))

    def test_sticker_renders(self):
        out = self._basic(mode="sticker", theme="dark")
        self.assertTrue(out.startswith(b"RIFF") or b"WEBP" in out[:16])

    def test_all_themes(self):
        for th in ("dark", "light", "white", "purple", "blue", "telegram"):
            out = self._basic(mode="png", theme=th)
            self.assertGreater(len(out), 1000)

    def test_fancy_unicode_no_crash(self):
        # Math-bold + small-caps display names used to produce □ tofu.
        msgs = [{"name": "\u1d401\u1d00\u1d0b\u1d00", "text": "styled name \u2713"}]
        out = render_quote_card(msgs, None, mode="png")
        self.assertGreater(len(out), 1000)

    def test_devanagari_and_reply(self):
        msgs = [{"name": "बॉब", "text": "नमस्ते दोस्तों 👋"}]
        rp = {"name": "किसी ने", "text": "यह जवाब है", "media": True}
        out = render_quote_card(msgs, None, mode="png", reply_preview=rp,
                                timestamp="14:32")
        self.assertGreater(len(out), 1000)

    def test_border_toggle(self):
        base = len(self._basic(mode="png", border=True))
        nb = len(self._basic(mode="png", border=False))
        self.assertGreater(base, 1000)
        self.assertGreater(nb, 1000)

    def test_dynamic_sizing(self):
        from PIL import Image
        import io as _io
        # Short message -> compact, min-width card (no wasted blank space).
        short = render_quote_card([{"name": "Al", "text": "hi"}], None,
                                  mode="png", timestamp="9:05")
        im = Image.open(_io.BytesIO(short))
        self.assertEqual(im.size[0], 280)          # MIN_W
        self.assertLess(im.size[1], 512)           # height follows content
        # Sticker stays a 512x512 square regardless of content.
        st = render_quote_card([{"name": "Al", "text": "hi"}], None,
                               mode="sticker")
        self.assertEqual(Image.open(_io.BytesIO(st)).size, (512, 512))

    def test_empty_raises(self):
        with self.assertRaises(QuoteRenderError):
            render_quote_card([], None)

    def test_glyph_fallback(self):
        from utils.quote_render import _pick_text_font, _dejavu
        noto = _pick_text_font(40)
        dj = _dejavu(40)
        # NotoSans lacks these but DejaVu has them; fallback must find a font.
        for ch in ("\u21bb", "\u2713", "\u2600", "\u2766"):
            self.assertTrue(_has_glyph(dj, ch))


if __name__ == "__main__":
    unittest.main()
