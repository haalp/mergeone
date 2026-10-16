package server

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/haalp/mergeone/gate/internal/memory"
)

const indexQuery = `query($owner:String!,$name:String!,$after:String){
  repository(owner:$owner,name:$name){
    pullRequests(first:50,after:$after,orderBy:{field:CREATED_AT,direction:DESC}){
      pageInfo{hasNextPage endCursor}
      nodes{ number title body author{login} state createdAt mergedAt closedAt updatedAt baseRefName headRefName
             files(first:100){nodes{path additions deletions}} }
    }
  }
}`

// Index backfills a repository's PR memory from GitHub (GraphQL, 50 PRs per call,
// newest first) back to `since`. Returns the number of entries written.
func (g *GitHub) Index(mem *memory.Store, repo string, since time.Time, progress func(n int)) (int, error) {
	owner, name, ok := splitRepo(repo)
	if !ok {
		return 0, fmt.Errorf("repo must be owner/name")
	}
	r := mem.Repo(repo)
	var after any
	n := 0
	for {
		body := map[string]any{"query": indexQuery, "variables": map[string]any{"owner": owner, "name": name, "after": after}}
		raw, code, err := g.do("POST", g.API+"/graphql", "application/json", body)
		if err != nil || code != 200 {
			return n, fmt.Errorf("graphql: %d %v %.200s", code, err, raw)
		}
		var out struct {
			Data struct {
				Repository struct {
					PullRequests struct {
						PageInfo struct {
							HasNextPage bool   `json:"hasNextPage"`
							EndCursor   string `json:"endCursor"`
						} `json:"pageInfo"`
						Nodes []struct {
							Number int    `json:"number"`
							Title  string `json:"title"`
							Body   string `json:"body"`
							Author struct {
								Login string `json:"login"`
							} `json:"author"`
							State     string `json:"state"`
							CreatedAt string `json:"createdAt"`
							MergedAt  string `json:"mergedAt"`
							ClosedAt  string `json:"closedAt"`
							UpdatedAt string `json:"updatedAt"`
							Base      string `json:"baseRefName"`
							Head      string `json:"headRefName"`
							Files     struct {
								Nodes []struct {
									Path      string `json:"path"`
									Additions int    `json:"additions"`
									Deletions int    `json:"deletions"`
								} `json:"nodes"`
							} `json:"files"`
						} `json:"nodes"`
					} `json:"pullRequests"`
				} `json:"repository"`
			} `json:"data"`
			Errors []struct {
				Message string `json:"message"`
			} `json:"errors"`
		}
		if err := json.Unmarshal(raw, &out); err != nil {
			return n, err
		}
		if len(out.Errors) > 0 && len(out.Data.Repository.PullRequests.Nodes) == 0 {
			return n, fmt.Errorf("graphql: %s", out.Errors[0].Message)
		}
		prs := out.Data.Repository.PullRequests
		for _, p := range prs.Nodes {
			created, _ := time.Parse(time.RFC3339, p.CreatedAt)
			if !created.IsZero() && created.Before(since) {
				return n, nil
			}
			body := p.Body
			if len(body) > 4000 {
				body = body[:4000]
			}
			e := memory.Entry{Number: p.Number, Title: p.Title, Body: body, Author: p.Author.Login, State: p.State, CreatedAt: p.CreatedAt,
				MergedAt: p.MergedAt, ClosedAt: p.ClosedAt, UpdatedAt: p.UpdatedAt, Base: p.Base, HeadRef: p.Head}
			for _, f := range p.Files.Nodes {
				e.Files = append(e.Files, memory.FileStat{Path: f.Path, Insertions: f.Additions, Deletions: f.Deletions})
			}
			if err := r.Upsert(e); err != nil {
				return n, err
			}
			n++
		}
		if progress != nil {
			progress(n)
		}
		if !prs.PageInfo.HasNextPage {
			return n, nil
		}
		after = prs.PageInfo.EndCursor
	}
}

func splitRepo(repo string) (string, string, bool) {
	for i := 0; i < len(repo); i++ {
		if repo[i] == '/' {
			return repo[:i], repo[i+1:], i > 0 && i < len(repo)-1
		}
	}
	return "", "", false
}
