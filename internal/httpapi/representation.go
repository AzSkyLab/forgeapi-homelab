package httpapi

import (
	"mime"
	"regexp"
	"strconv"
	"strings"
)

var quality = regexp.MustCompile(`^(0(\.[0-9]{0,3})?|1(\.0{0,3})?)$`)

// accepts applies media-range specificity before quality (RFC 9110 section
// 12.5.1). A specific q=0 exclusion cannot be overridden by a wildcard.
func accepts(header, representation string) bool {
	if header == "" {
		return true
	}
	best, q := -1, 0.0
	// Split only outside quoted parameter values.
	var entries []string
	start, quoted, escaped := 0, false, false
	for i, c := range header {
		if escaped {
			escaped = false
			continue
		}
		if quoted && c == '\\' {
			escaped = true
			continue
		}
		if c == '"' {
			quoted = !quoted
		}
		if c == ',' && !quoted {
			entries = append(entries, header[start:i])
			start = i + 1
		}
	}
	if quoted {
		return false
	}
	entries = append(entries, header[start:])
	for _, entry := range entries {
		media, params, err := mime.ParseMediaType(strings.TrimSpace(entry))
		if err != nil {
			return false
		}
		weight := 1.0
		if value, ok := params["q"]; ok {
			if !quality.MatchString(value) {
				return false
			}
			weight, _ = strconv.ParseFloat(value, 64)
			delete(params, "q")
		}
		if len(params) != 0 {
			continue
		} // No response media parameters.
		specificity := -1
		switch media {
		case "*/*":
			specificity = 0
		case strings.SplitN(representation, "/", 2)[0] + "/*":
			specificity = 1
		case representation:
			specificity = 2
		}
		if specificity > best || (specificity == best && weight > q) {
			best, q = specificity, weight
		}
	}
	return best >= 0 && q > 0
}

func matchesETag(header, current string) bool {
	for _, tag := range strings.Split(header, ",") {
		tag = strings.TrimSpace(tag)
		if tag == "*" || strings.TrimPrefix(tag, "W/") == current {
			return true
		}
	}
	return false
}
