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

    def test_hidden_elements_are_left_out(self):
        # A hidden "We sponsor" read yes; a hidden line can also talk to the reader or the judge (#112).
        for opener in ('<div hidden>', '<div style="display:none">',
                       '<div style="DISPLAY : None">', '<div style="visibility:hidden">',
                       '<div style="font-size:0px">', '<div style="color:transparent">',
                       '<div style="position:absolute;left:-9999px">', '<div style="text-indent:-10000px">',
                       '<div style="clip:rect(0 0 0 0)">', '<div style="clip-path:inset(100%)">',
                       '<div style="height:0;overflow:hidden">', '<div style="display&#58;none">',
                       '<div style="display:/**/none">'):
            self.assertEqual(html_to_text(f"<p>Great team.</p>{opener}We sponsor H-1B visas.</div><p>Apply.</p>"),
                             "Great team.\nApply.", opener)

    def test_everyday_styles_are_not_hidden(self):
        # Webflow puts opacity:0 on every block that fades in, so it is not hidden.
        for opener in ('<div style="opacity:0">', '<div style="opacity:0.5">', '<div style="height:0">',
                       '<div style="overflow:hidden">', '<div style="font-size:0.9em">', '<div style="left:-20px">',
                       '<div aria-hidden="true">', '<div class="hidden-xs">'):
            # aria-hidden only hides from a screen reader: the text is on the screen, so the rules read it.
            self.assertEqual(html_to_text(f"{opener}We sponsor H-1B visas.</div>"), "We sponsor H-1B visas.", opener)

    def test_a_hidden_element_ends_at_its_own_closing_tag(self):
        markup = '<div hidden><div>a</div><div>b</div>c</div>visible'
        self.assertEqual(html_to_text(markup), "visible")
        self.assertEqual(html_to_text('<section><span hidden>x</span>shown</section>'), "shown")

    def test_a_closing_parent_ends_an_unclosed_hidden_child(self):
        self.assertEqual(html_to_text('<ul><li hidden>x</ul><p>visible</p>'), "visible")

    def test_implied_ends_close_a_hidden_element(self):
        self.assertEqual(html_to_text('<ul><li hidden>x<li>shown</ul>'), "shown")
        self.assertEqual(html_to_text('<p hidden>x<div>shown</div>'), "shown")
        self.assertEqual(html_to_text('<table><tr><td hidden>x<td>shown</tr></table>'), "shown")

    def test_a_self_closing_slash_on_a_normal_tag_is_ignored_as_browsers_do(self):
        self.assertEqual(html_to_text('<div hidden/>secret</div>shown'), "shown")

    def test_a_hidden_void_tag_hides_nothing_after_it(self):
        self.assertEqual(html_to_text('<img hidden src="x.png">shown<br hidden>too'), "shown\ntoo")
        self.assertEqual(html_to_text('<input type="hidden" value="x">shown'), "shown")

    def test_template_and_svg_are_dropped(self):
        self.assertEqual(html_to_text('<template>We sponsor</template><svg><text>logo</text></svg>shown'), "shown")

    def test_invisible_characters_are_dropped(self):
        self.assertEqual(html_to_text("<p>We do n‍ot spon&shy;sor\U000E0041\U000E0042 visas.</p>"),
                         "We do not sponsor visas.")
        self.assertEqual(html_to_text("a b"), "a\nb")

    def test_visible_only_false_reads_the_page_as_before(self):
        markup = '<p>a</p><div hidden>b</div><template>c</template><svg><text>d</text></svg><p>e\u200bf</p>'
        self.assertEqual(html_to_text(markup, visible_only=False), "a\nb\ncd\ne\u200bf")
        self.assertEqual(html_to_text(markup), "a\nef")

    def test_empty_and_markup_only(self):
        self.assertEqual(html_to_text(""), "")
        self.assertEqual(html_to_text("<div><br></div>"), "")

    def test_broken_markup_does_not_raise(self):
        self.assertEqual(html_to_text("<p>open <b>bold <i>x"), "open bold x")
        self.assertIn("a", html_to_text("a <unclosed"))


if __name__ == "__main__":
    unittest.main()
