export function editPost(user, post, title) {
  if (user.id !== post.ownerId && user.role !== "admin") throw new Error("forbidden");
  return { ...post, title };
}
