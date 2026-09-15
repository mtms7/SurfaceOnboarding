# Public Suffix List data

`public_suffix_list.dat` is a pinned deployment asset retrieved from the
official Public Suffix List URL:

`https://publicsuffix.org/list/public_suffix_list.dat`

The file is distributed under Mozilla Public License 2.0. The running service
does not download or refresh it. A new snapshot must be reviewed, tested, and
deployed as a versioned change; its SHA-256 is returned by the preflight API.

