"""Code shared by every stage package (A_data_prepare ... G_report).

Stages never import each other; each may import `common`. Stages are linked only through files under data/.
"""
