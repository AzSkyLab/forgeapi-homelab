// Package keyvault contains the one reviewed pattern supported by the local spike.
// Arbitrary repositories, remote modules and user-supplied HCL are not accepted.
package keyvault

import (
	"crypto/sha256"
	"embed"
	"fmt"
)

//go:embed main.tf variables.tf .terraform.lock.hcl
var Files embed.FS

func Digest() string {
	h := sha256.New()
	for _, name := range []string{"main.tf", "variables.tf", ".terraform.lock.hcl"} {
		b, err := Files.ReadFile(name)
		if err != nil {
			panic(err)
		}
		fmt.Fprintf(h, "%s\x00%d\x00", name, len(b))
		h.Write(b)
	}
	return fmt.Sprintf("sha256:%x", h.Sum(nil))
}
