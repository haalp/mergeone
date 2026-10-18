package server

import (
	"container/list"
	"fmt"
	"strings"
	"sync"
)

// diffCache keeps the per-file stats and the unified diff of recently decided PR heads, keyed by
// repo#pr@sha. A head sha's files and diff do not change, so a re-decision of the same head (reopened,
// ready_for_review, a retried job) skips the files and diff requests; the PR metadata (title, body,
// timestamps) is always fetched fresh, so the rendered state is the same as without the cache.
type diffCache struct {
	mu  sync.Mutex
	max int
	ll  *list.List
	m   map[string]*list.Element
}

type diffEntry struct {
	key   string
	files []FileStat
	diff  string
}

func newDiffCache(max int) *diffCache {
	return &diffCache{max: max, ll: list.New(), m: map[string]*list.Element{}}
}

func diffKey(repo string, pr int, sha string) string { return fmt.Sprintf("%s#%d@%s", repo, pr, sha) }

func (c *diffCache) get(repo string, pr int, sha string) ([]FileStat, string, bool) {
	if c == nil || sha == "" {
		return nil, "", false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	el, ok := c.m[diffKey(repo, pr, sha)]
	if !ok {
		return nil, "", false
	}
	c.ll.MoveToFront(el)
	e := el.Value.(*diffEntry)
	return append([]FileStat(nil), e.files...), e.diff, true
}

func (c *diffCache) put(repo string, pr int, sha string, files []FileStat, diff string) {
	if c == nil || sha == "" {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	k := diffKey(repo, pr, sha)
	if el, ok := c.m[k]; ok {
		c.ll.MoveToFront(el)
		el.Value = &diffEntry{k, append([]FileStat(nil), files...), diff}
		return
	}
	c.m[k] = c.ll.PushFront(&diffEntry{k, append([]FileStat(nil), files...), diff})
	for c.ll.Len() > c.max {
		old := c.ll.Back()
		c.ll.Remove(old)
		delete(c.m, old.Value.(*diffEntry).key)
	}
}

// invalidate drops every cached head of repo#pr except keepSHA (a synchronize to a new head).
func (c *diffCache) invalidate(repo string, pr int, keepSHA string) {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	prefix := fmt.Sprintf("%s#%d@", repo, pr)
	for k, el := range c.m {
		if strings.HasPrefix(k, prefix) && k != prefix+keepSHA {
			c.ll.Remove(el)
			delete(c.m, k)
		}
	}
}

func (c *diffCache) len() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.ll.Len()
}
