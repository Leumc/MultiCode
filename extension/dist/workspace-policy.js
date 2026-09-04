// src/workspace-policy.ts
import * as path from "node:path";
function isWorkspaceCppFile(candidate, roots) {
  if (candidate.scheme !== "file" || !candidate.path.endsWith(".cpp")) {
    return false;
  }
  const candidatePath = path.posix.resolve(candidate.path);
  return roots.some((root) => {
    if (root.scheme !== candidate.scheme) {
      return false;
    }
    const rootPath = path.posix.resolve(root.path);
    const relative = path.posix.relative(rootPath, candidatePath);
    return relative !== "" && relative !== ".." && !relative.startsWith("../") && !path.posix.isAbsolute(relative);
  });
}
export {
  isWorkspaceCppFile
};
