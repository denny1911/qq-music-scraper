Run python init_yt_mapping.py
/opt/hostedtoolcache/Python/3.10.20/x64/lib/python3.10/site-packages/google/api_core/_python_version_support.py:254: FutureWarning: You are using a Python version (3.10.20) which Google will stop supporting in new releases of google.api_core once it reaches its end of life (2026-10-04). Please upgrade to the latest Python version, or at least Python 3.11, to continue receiving updates for google.api_core past that date.
  warnings.warn(message, FutureWarning)
Traceback (most recent call last):
📁 發現 48 個歷史榜單檔案，正在彙整歷史歌曲...
  File "/opt/hostedtoolcache/Python/3.10.20/x64/lib/python3.10/site-packages/pandas/core/indexes/base.py", line 3812, in get_loc
📊 歷史榜單中共有 598 首不重複歌曲。
    return self._engine.get_loc(casted_key)
  File "pandas/_libs/index.pyx", line 167, in pandas._libs.index.IndexEngine.get_loc
  File "pandas/_libs/index.pyx", line 196, in pandas._libs.index.IndexEngine.get_loc
  File "pandas/_libs/hashtable_class_helper.pxi", line 7088, in pandas._libs.hashtable.PyObjectHashTable.get_item
  File "pandas/_libs/hashtable_class_helper.pxi", line 7096, in pandas._libs.hashtable.PyObjectHashTable.get_item
KeyError: 'Video ID'

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/home/runner/work/qq-music-scraper/qq-music-scraper/init_yt_mapping.py", line 343, in <module>
    init_yt_mapping()
  File "/home/runner/work/qq-music-scraper/qq-music-scraper/init_yt_mapping.py", line 117, in init_yt_mapping
    v_id = str(row["Video ID"]).strip()
  File "/opt/hostedtoolcache/Python/3.10.20/x64/lib/python3.10/site-packages/pandas/core/series.py", line 1133, in __getitem__
    return self._get_value(key)
  File "/opt/hostedtoolcache/Python/3.10.20/x64/lib/python3.10/site-packages/pandas/core/series.py", line 1249, in _get_value
    loc = self.index.get_loc(label)
  File "/opt/hostedtoolcache/Python/3.10.20/x64/lib/python3.10/site-packages/pandas/core/indexes/base.py", line 3819, in get_loc
    raise KeyError(key) from err
KeyError: 'Video ID'
Error: Process completed with exit code 1.
