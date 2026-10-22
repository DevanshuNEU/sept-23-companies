"""Plain-text bodies (issue #21, Task 3)."""
import html
import unittest

from outreach.crawl.text import html_to_text
from outreach.rules.text import sentences


class HtmlToTextTests(unittest.TestCase):
    def test_list_items_become_lines(self):
        self.assertEqual(html_to_text("<ul><li>We do not sponsor visas</li><li>Remote</li></ul>"),
                         "We do not sponsor visas\nRemote")

    def test_the_sentence_splitter_sees_a_stop_between_list_items(self):
        out = html_to_text("<ul><li>No sponsorship</li><li>Remote</li></ul>")
        self.assertEqual(len(sentences(out)), 2)

    def test_block_tags_and_br_break_lines(self):
        for tag in ("p", "div", "tr", "h1", "h3", "h6", "ul", "ol", "section"):
            self.assertEqual(html_to_text(f"a<{tag}>b</{tag}>c"), "a\nb\nc", tag)
        self.assertEqual(html_to_text("a<br>b<br/>c"), "a\nb\nc")

    def test_table_cells_do_not_run_words_together(self):
        self.assertEqual(html_to_text("<table><tr><td>Visa</td><td>No sponsorship</td></tr></table>"),
                         "Visa\nNo sponsorship")
        self.assertEqual(html_to_text("<tr><th>a</th><th>b</th></tr>"), "a\nb")

    def test_definition_lists_do_not_run_words_together(self):
        self.assertEqual(html_to_text("<dl><dt>Pay</dt><dd>$100k</dd></dl>"), "Pay\n$100k")

    def test_more_block_tags_break_lines(self):
        for tag in ("blockquote", "pre", "article", "header", "footer", "table", "dl", "dd", "dt", "td", "th"):
            self.assertEqual(html_to_text(f"a<{tag}>b</{tag}>c"), "a\nb\nc", tag)

    def test_hr_breaks_lines(self):
        self.assertEqual(html_to_text("above<hr>below"), "above\nbelow")
        self.assertEqual(html_to_text("above<hr/>below"), "above\nbelow")

    def test_script_style_and_comments_are_dropped(self):
        markup = ("<p>Hi</p><script>var x = 'secret';</script><style>p { color: red }</style>"
                  "<noscript>enable js</noscript><!-- hidden note --><p>There</p>")
        self.assertEqual(html_to_text(markup), "Hi\nThere")

    def test_a_tag_inside_script_text_does_not_end_the_skip_early(self):
        self.assertEqual(html_to_text("<script>if (a<b) { x = '</p>'; }</script>ok"), "ok")

    def test_entities_are_decoded(self):
        self.assertEqual(html_to_text("R&amp;D &nbsp;team"), "R&D team")
        self.assertEqual(html_to_text("caf&eacute; &#169; &#x41;"), "café © A")

    def test_nul_is_removed(self):
        self.assertEqual(html_to_text("a\x00b<p>c\x00</p>"), "ab\nc")

    def test_greenhouse_double_escaped_content(self):
        self.assertEqual(html_to_text(html.unescape("&lt;p&gt;Hi&lt;/p&gt;")), "Hi")

    def test_whitespace_is_collapsed(self):
        self.assertEqual(html_to_text("<p>a \t  b</p>\n\n\n<p>c</p><p></p><p></p><p>d</p>"), "a b\n\nc\nd")
        self.assertEqual(html_to_text("one <p> two </p> three"), "one\ntwo\nthree")

    def test_three_or_more_line_breaks_become_two(self):
        self.assertEqual(html_to_text("a\n\n\n\n\nb"), "a\n\nb")

    def test_plain_text_passes_through(self):
        self.assertEqual(html_to_text("  Just text, 3 < 5 and 5 > 3.  "), "Just text, 3 < 5 and 5 > 3.")

    def test_empty_and_markup_only(self):
        self.assertEqual(html_to_text(""), "")
        self.assertEqual(html_to_text("<div><br></div>"), "")

    def test_broken_markup_does_not_raise(self):
        self.assertEqual(html_to_text("<p>open <b>bold <i>x"), "open bold x")
        self.assertIn("a", html_to_text("a <unclosed"))


if __name__ == "__main__":
    unittest.main()
