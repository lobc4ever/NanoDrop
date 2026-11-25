"""Offline retrieval, ranking and candidate integrity checks."""
from types import SimpleNamespace
from unittest.mock import patch
from credentials import SearchAI
import music_search as search
from music_search import Candidate, Recording


@patch.object(search,'netease_choices',return_value=[])
@patch.object(search,'qq_choices',return_value=[])
def exercise(_qq,_netease):
    track=SimpleNamespace(title='03 - 小宇 - 昨夭 [320kbps].flac',artist='',album='测试专辑',length_ms=195000)
    assert search.search_terms(track)==('昨夭','小宇')
    assert search.search_terms(SimpleNamespace(title='99 Luftballons',artist='Nena'))==('99 Luftballons','Nena')
    assert search.similarity('Yelow','Yellow')>.85
    assert search.similarity('小小','小小的太阳')>.7
    assert ('The Long Way','') in search.variants('The Long Way Home (Live)','Singer')
    right=Candidate('昨天','小宇','测试专辑',195,'目录','cover')
    live=Candidate('昨天 (Live)','小宇','现场',229,'目录','live')
    unrelated=Candidate('游乐场','别人','其他',289,'目录','other')
    words=Candidate(right.title,right.artist,right.album,194,'歌词目录','fixture')
    different=Candidate(right.title,right.artist,right.album,225,'歌词目录','different take')
    rows=search.group_results([right,live],[words,different])
    assert len(rows)==3 and rows[0].lyric==words and any(r.lyric==different and not r.cover for r in rows)
    calls=[]
    def catalog(t,a):
        calls.append((t,a))
        return [live,right,unrelated] if t=='昨天' else []
    def suggest(track,candidates,key,request):
        return [],('昨天','小宇')
    def lookup():
        return search.lookup(track,lambda _:None)
    with patch.object(search,'cover_choices',catalog),patch.object(search,'lyric_choices',return_value=[]), \
         patch.object(search,'archive_choices',return_value=[]),patch.object(search,'read_settings',return_value=SearchAI(key='fixture')), \
         patch.object(search,'advice',suggest):
        found,note=lookup()
        assert ('昨天','') in calls and all(not a for t,a in calls) and found[0].entry==right and 'DeepSeek' in note
        assert live in [r.entry for r in found]
        with patch.object(search,'cover_choices',return_value=[]):
            assert lookup()[0]==[]
    with patch.object(search,'read_settings',return_value=SearchAI()),patch.object(search,'cover_choices',return_value=[unrelated,live,right]), \
         patch.object(search,'archive_choices',return_value=[]),patch.object(search,'lyric_choices',return_value=[]):
        found,note=lookup()
        assert found[0].entry==right
    calls.clear()
    def artists(t,a):
        calls.append((t,a))
        return [right,unrelated,live] if a=='小宇' else []
    with patch.object(search,'read_settings',return_value=SearchAI(key='fixture')),patch.object(search,'advice',suggest), \
         patch.object(search,'cover_choices',artists),patch.object(search,'archive_choices',return_value=[]),patch.object(search,'lyric_choices',return_value=[]):
        found,note=search.lookup(track,lambda _:None,search.SearchRequest('artist','小羽'))
        assert ('','小羽') in calls and ('','小宇') in calls and all(not t for t,a in calls)
        assert {r.entry for r in found}=={right,live}
        calls.clear()
        assert search.lookup(track,lambda _:None,search.SearchRequest('artist',''))[0]==[] and not calls
    with patch.object(search,'lyric_choices',return_value=[different,words]):
        assert search.complete_recording(Recording(right,right)).lyric==words
    print('Fuzzy query cleanup, typo and substring ranking, different recordings, model retry, catalog-only titles, offline ranking and release-specific lyrics passed.')


if __name__=='__main__':
    exercise()
