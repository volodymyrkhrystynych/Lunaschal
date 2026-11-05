"""Print what one reader page says about your own reactions.

The reaction markup is read from XenForo 2's default action bar, which a
forum theme can change; this is how to check it against the real site before
trusting a "not reacted". Fetches the page through the saved session cookies,
exactly as a download would, and stores nothing from it.

    python -m backend.fanfic.reaction_probe 'https://forums.spacebattles.com/threads/x.123/reader/'
"""
import sys

from backend.fanfic import download, xenforo


def probe(url: str) -> str:
    page = xenforo.parse_reader_page(download._fetch(url).text)
    lines = [f'logged in: {page.logged_in}  posts: {len(page.posts)}']
    for post in page.posts:
        state = (post.my_reaction or 'not reacted') if post.reaction_seen else 'no react button'
        lines.append(f'  post-{post.post_id}  {post.threadmark_title[:50]:<50}  {state}')
    if not page.logged_in:
        lines.append('Not logged in: save this site\'s cookies in Settings -> Fanfic site cookies.')
    return '\n'.join(lines)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(probe(sys.argv[1]))
